# -*- coding: utf-8 -*-
"""
直播切片 · 剪映草稿模板生成器
================================================
把《剪辑手册》里的参数固化成代码，生成一份剪映能直接打开的草稿工程。

用法：
    python make_draft.py --demo                       # 生成演示草稿（自带测试视频）
    python make_draft.py --video 你的素材.mp4          # 用你的视频生成模板
    python make_draft.py --video 素材.mp4 --srt 字幕.srt --name 第一条切片

生成后打开剪映 → 草稿列表里就会出现这份工程。
"""

import argparse
import json
import os
import re
import sys
import uuid
from datetime import timedelta

from pyJianYingDraft import (
    ClipSettings, DraftFolder, TextBackground, TextBorder, TextSegment,
    TextStyle, Timerange, TrackSpec, TrackType, VideoSceneEffectType,
    VideoSegment, trange,
)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ============================================================
#  一、参数区 —— 全部来自《剪辑手册》，改这里就等于改全片风格
# ============================================================

# 剪映草稿根目录（剪映 → 设置 → 草稿位置，一般是这个）
DRAFT_ROOT = os.path.join(
    os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"),
    "JianyingPro", "User Data", "Projects", "com.lveditor.draft",
)

# —— 版式 ——
#  "blur"  : 竖屏 1080×1920 + 模糊背景铺满 + 视频居中偏上（发抖音的默认，最好看）
#  "split" : 竖屏分栏 —— 视频贴顶、大字幕在下方黑区（纯黑底，比较硬）
#  "fill"  : 跟素材走 —— 画布比例与源视频一致，视频满屏（横屏直出，不加第二套字幕时最稳）
#  "auto"  : 横屏素材 → blur；竖屏素材 → fill
LAYOUT = "auto"

CANVAS_W, CANVAS_H, FPS = 1080, 1920, 30
SRC_W, SRC_H = 1920, 1080      # 源视频分辨率，由 probe_video() 探测后覆盖
VIDEO_Y = 0.0                  # 视频轨垂直位移（fill 版式下为 0）
VIDEO_SCALE = 1.0              # 视频轨缩放（blur 版式下轻微放大，画面更饱满）
TITLE_Y = 0.20                 # 标题位置

# —— 模糊背景（blur 版式）——
USE_BG = False                 # 是否铺模糊背景，由 apply_layout() 决定
BG_SCALE = 1.0                 # 背景放大倍数，铺满画布用
BG_ALPHA = 0.62                # 背景不透明度，压暗一点，别抢前景（0.62≈压暗 38%）
BG_BLUR = 35                   # 背景模糊强度，0–100（就是剪映「模糊」特效那根滑杆的量程）
                               #   注意 pyJianYingDraft 的 add_effect 收的是 0–100，
                               #   写 0.8 会被当成 0.8%（= 压根没糊）。这里按 0–100 给。
                               #   0 = 不糊（只剩放大）/ 20 很轻 / 35 轻（默认，跟放大压暗配合刚好）
                               #   55 中 / 80 强（只剩色块）/ 100 极强（一团色雾）
                               # 界面里「背景模糊」下拉就是改这个值，前景清晰度完全不受影响

# —— 抹掉画面里烧死的原字幕 ——
#  直播录屏的原字幕压在画面底部 83%–90%，跟下方新字幕重复。
#  做法是先用 clean_subs.py 把这一条横带用 delogo 补平，生成一份 -clean.mp4，
#  草稿直接引用这份视频 —— 画面比例完全不变，上下都不会少一块。
#  早先试过「矩形蒙版裁掉底部 18%」，画面会变成 2.1:1，放在竖屏里又扁又矮，已弃用。
CUT_ORIG_SUBS = True           # 是否抹掉画面里的原字幕

# —— 主字幕 ——
SUB_SIZE = 9.0              # 字号（手册：60–72px 视觉大小，剪映内部用这个刻度）
SUB_COLOR = (1.0, 1.0, 1.0)  # 纯白
SUB_BOLD = True
SUB_BORDER_W = 25.0         # 黑描边（手册：3–4px）
SUB_Y = -0.40               # 垂直位置，-0.8 是最底部；-0.4 ≈ 画面 70% 处
SUB_MAX_LINE = 9            # 单行最多 9 字，超了自动断句

# —— 花字 ——
FANCY_SIZE = 16.0
FANCY_Y = 0.05              # 花字放画面中部
FANCY_BG_COLOR = "#FFD84D"  # 亮黄底
FANCY_TEXT_COLOR = (0.12, 0.11, 0.09)  # 深色字

# —— 提示轨（把手册的"音效落点"直接标在时间轴上）——
HINT_SIZE = 6.0
HINT_Y = 0.72               # 提示放在画面上方，不挡字幕
HINT_BG_COLOR = "#F0997B"
HINT_COLOR = (1.0, 1.0, 1.0)


# ============================================================
#  二、工具函数
# ============================================================

TITLE_SIZE = 15.0             # 竖屏上方空白区够大，标题可以更抢眼
TITLE_COLOR = (1.0, 0.86, 0.30)


def probe_video(path: str):
    """读源视频的分辨率与时长，返回 (w, h, dur_us)。读不到就按 16:9 兜底。"""
    import subprocess
    w = h = 0
    dur = 0
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        r = subprocess.run([ff, "-i", path], capture_output=True, text=True,
                           errors="ignore", timeout=60)
        txt = r.stderr
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
        if m:
            dur = int((int(m.group(1)) * 3600 + int(m.group(2)) * 60
                       + float(m.group(3))) * 1_000_000)
        m = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", txt)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
    except Exception as e:
        print(f"  [警告] 探测视频信息失败：{e}")
    if not w or not h:
        w, h = 1920, 1080
    if not dur:
        dur = video_duration_us(path)
    return w, h, dur


def apply_layout(layout: str = "auto", src_w: int = 1920, src_h: int = 1080):
    """按源视频方向和版式，算出画布尺寸、视频位置、字幕位置。

    blur（横屏素材发抖音的默认，推荐）:
        1080×1920 画布。底层铺一份放大 + 模糊、压暗的同一段视频把画布填满，
        原本的横屏画面缩到画布宽度、中心落在 40% 高度（中间偏上）。
        上面 12% 处放标题/钩子大字，下面 72% 处放新字幕 —— 三段互不打架。
        比 split 好看的原因：上下不再是死黑，画面延伸成背景，视觉是一整块。
    split（竖屏分栏）:
        1080×1920 画布，视频按宽度铺满摆在最上方，
        原视频烧在画面里的字幕会被缩到很小、留在上方视频区内，
        新字幕写进下方黑区 —— 两套字幕空间上分开，不再叠在一起。
    fill（横屏满屏）:
        画布 = 源视频比例，视频铺满，新字幕只能叠在画面上，
        所以要往上挪到画面 60% 处，避开原字幕所在的 72%–79%。
    """
    global LAYOUT, CANVAS_W, CANVAS_H, SRC_W, SRC_H
    global VIDEO_Y, VIDEO_SCALE, SUB_Y, TITLE_Y, USE_BG, BG_SCALE
    layout = (layout or "auto").lower()
    if layout == "auto":
        # 横屏素材发抖音 → 必须转 9:16，用模糊背景版式；竖屏素材本来就对，照原样满屏
        layout = "blur" if src_w > src_h else "fill"
    LAYOUT, SRC_W, SRC_H = layout, src_w, src_h
    USE_BG, BG_SCALE, VIDEO_SCALE = False, 1.0, 1.0

    if layout == "blur":
        CANVAS_W, CANVAS_H = 1080, 1920
        fit_w = min(CANVAS_W, CANVAS_H * src_w / src_h)   # 素材完整显示时的宽
        fit_h = fit_w * src_h / src_w
        USE_BG = True
        # 不放大：放大是围绕画面中心的，顶部会跟着被裁掉 —— 实测 1.15 倍后
        # 两个人的额头都不见了。宁可画面小一点，也要保住人脸完整。
        VIDEO_SCALE = 1.0
        BG_SCALE = max(CANVAS_W / fit_w, CANVAS_H / fit_h) * 1.02   # 多放 2%，防边缘露黑
        VIDEO_Y = 0.12          # 视频中心落在画布 44% 处（中间偏上）
        TITLE_Y = 0.74          # 标题在 13% 处，视频上方空白区
        SUB_Y = -0.52           # 字幕在 76% 处，视频下方空白区，且避开抖音底部按钮
    elif layout == "fill":
        CANVAS_W, CANVAS_H = (1920, 1080) if src_w >= src_h else (1080, 1920)
        VIDEO_Y = 0.0
        SUB_Y = -0.20            # 画面 60% 处，避开原字幕所在的 72%–79%
        TITLE_Y = 0.72
    else:
        CANVAS_W, CANVAS_H = 1080, 1920
        shown_h = CANVAS_W * src_h / src_w          # 视频按宽度铺满后的高度
        if shown_h > CANVAS_H:                       # 竖屏素材，改成按高度铺满
            shown_h = CANVAS_H
        center_frac = shown_h / 2 / CANVAS_H
        VIDEO_Y = 1 - 2 * center_frac               # 视频贴到最上方
        free = max(CANVAS_H - shown_h, 1)
        TITLE_Y = 1 - 2 * ((shown_h + free * 0.14) / CANVAS_H)
        SUB_Y = 1 - 2 * ((shown_h + free * 0.42) / CANVAS_H)
    return layout


def video_duration_us(path: str) -> int:
    """读取视频时长（微秒）。pymediainfo 读不出来时给 15 秒兜底。"""
    try:
        from pymediainfo import MediaInfo
        info = MediaInfo.parse(path)
        for track in info.tracks:
            if track.track_type == "Video" and track.duration:
                return int(float(track.duration) * 1000)
    except Exception as e:
        print(f"  [警告] 读取视频时长失败：{e}")
    return 15_000_000


def parse_srt(path: str):
    """极简 SRT 解析，返回 [(start_us, end_us, text), ...]"""
    with open(path, "r", encoding="utf-8-sig") as f:
        raw = f.read().replace("\r\n", "\n")

    def to_us(stamp: str) -> int:
        h, m, rest = stamp.strip().split(":")
        s, ms = rest.split(",")
        return (int(h) * 3600 + int(m) * 60 + int(s)) * 1_000_000 + int(ms) * 1000

    out = []
    for block in raw.split("\n\n"):
        lines = [ln for ln in block.strip().split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        m = re.search(r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})", block)
        if not m:
            continue
        text = " ".join(lines[2:]) if len(lines) > 2 else ""
        text = re.sub(r"<[^>]+>", "", text).strip()
        if text:
            out.append((to_us(m.group(1)), to_us(m.group(2)), text))
    return out


def split_long_lines(text: str, max_len: int = SUB_MAX_LINE):
    """按意群断句：先按标点断，再按长度硬切，保证单行不超过 max_len 字。"""
    parts = re.split(r"(?<=[，。！？、；：,.!?])", text)
    result, buf = [], ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(buf) + len(p) <= max_len:
            buf += p
        else:
            if buf:
                result.append(buf)
            while len(p) > max_len:
                result.append(p[:max_len])
                p = p[max_len:]
            buf = p
    if buf:
        result.append(buf)
    return result or [text]


SUB_MIN_US = 150_000     # 一行字幕最短停留 0.15 秒，再短就并入相邻行


def plan_subtitles(text: str, dur_us: int, max_len: int = SUB_MAX_LINE):
    """把一句话拆成若干行，并把 dur_us 精确分配给每一行。

    返回 [(行文字, 相对起点, 时长), ...]，三条硬保证：
      1. 各行时长相加 == dur_us（不多不少，所以不会顶到下一句上）
      2. 行与行首尾相接，不重叠
      3. 不会为了凑"最短时长"而越界 —— 太短的行是把文字并进相邻行，
         而不是像以前那样硬撑到 0.2 秒、把后面的字幕撞掉。
    """
    if dur_us <= 0:
        return []
    lines = [l for l in split_long_lines(text, max_len) if l.strip()] or ["　"]

    # 反复把"按比例算下来太短"的行并入相邻行，直到都够长（或只剩一行）
    while len(lines) > 1:
        weights = [max(len(l), 1) for l in lines]
        wsum = sum(weights)
        short = [i for i, w in enumerate(weights)
                 if int(dur_us * w / wsum) < SUB_MIN_US]
        if not short:
            break
        i = short[-1]
        if i == 0:
            lines[1] = lines[0] + lines[1]
            del lines[0]
        else:
            lines[i - 1] = lines[i - 1] + lines[i]
            del lines[i]

    weights = [max(len(l), 1) for l in lines]
    wsum = sum(weights)
    out, acc = [], 0
    for i, (line, w) in enumerate(zip(lines, weights)):
        d = (dur_us - acc) if i == len(lines) - 1 else int(dur_us * w / wsum)
        if d <= 0:
            continue
        out.append((line, acc, d))
        acc += d
    return out


# ============================================================
#  三、样式构造
# ============================================================

def main_subtitle_style() -> TextStyle:
    return TextStyle(
        size=SUB_SIZE, bold=SUB_BOLD, color=SUB_COLOR, align=1,
        auto_wrapping=True, max_line_width=0.86,
    )


MAIN_BORDER = TextBorder(width=SUB_BORDER_W, color=(0.0, 0.0, 0.0))


def main_clip() -> ClipSettings:
    """主字幕位置。用函数而不是常量，这样改版式后重新取值一定是最新的。"""
    return ClipSettings(transform_y=SUB_Y)


def video_clip() -> ClipSettings:
    """视频轨位置 + 缩放（blur 版式下居中偏上并轻微放大）。"""
    return ClipSettings(scale_x=VIDEO_SCALE, scale_y=VIDEO_SCALE,
                        transform_y=VIDEO_Y)


def make_bg_segment(video_path: str, t_start: int, dur: int, src_start: int = 0,
                    volume: float = 0.0):
    """造一段「放大 + 模糊 + 压暗」的背景片，用来把 9:16 画布填满。

    要点：
      · 缩放用等比，且放大到 max(宽比, 高比) —— 横屏素材铺满竖屏画布要放大 ~3.1 倍，
        超出画布的部分会被自动裁掉，正好形成满屏背景。
      · 模糊挂在片段上（add_effect），素材会在 add_segment 时自动收进 materials，
        顺序不用特别处理（这点和转场不同）。
      · alpha 压到 0.85：模糊后的背景如果太亮，会跟前景抢注意力。
      · 音量默认 0：背景是同一段视频的副本，放声音会和前景叠成回音。
    """
    seg = VideoSegment(
        video_path, trange(t_start, dur),
        source_timerange=trange(src_start, dur),
        volume=volume,
        clip_settings=ClipSettings(scale_x=BG_SCALE, scale_y=BG_SCALE, alpha=BG_ALPHA),
    )
    try:
        seg.add_effect(VideoSceneEffectType.模糊, [BG_BLUR])
    except Exception as e:
        print(f"  [警告] 背景模糊没加上（{e}），背景会是放大后的实画面")
    return seg


def add_title(sf, text: str, start_us: int, dur_us: int, track: str = "标题"):
    """片头标题，在 blur / split 版式下都放在画面上方的空白区。"""
    seg = TextSegment(
        text,
        Timerange(start_us, dur_us),
        style=TextStyle(size=TITLE_SIZE, bold=True, color=TITLE_COLOR, align=1,
                        auto_wrapping=True, max_line_width=0.86),
        border=TextBorder(width=SUB_BORDER_W * 0.6, color=(0.0, 0.0, 0.0)),
        clip_settings=ClipSettings(transform_y=TITLE_Y),
    )
    sf.add_segment(seg, track)


def add_main_subtitles(sf, srt_path: str, track_name: str = "主字幕"):
    """把 SRT 铺成主字幕轨，样式统一按手册来。"""
    sf.append_track(TrackSpec(TrackType.text, track_name))
    entries = parse_srt(srt_path)
    count = 0
    for start, end, text in entries:
        for line, off, d in plan_subtitles(text, max(0, end - start)):
            sf.add_segment(
                TextSegment(line, Timerange(start + off, d),
                            style=main_subtitle_style(), border=MAIN_BORDER,
                            clip_settings=main_clip()),
                track_name,
            )
            count += 1
    return count


def add_fancy_text(sf, text: str, start_us: int, dur_us: int, track: str = "花字"):
    """加一个花字（黄底深字），一条片子建议 ≤5 个。"""
    seg = TextSegment(
        text,
        Timerange(start_us, dur_us),
        style=TextStyle(size=FANCY_SIZE, bold=True, color=FANCY_TEXT_COLOR, align=1),
        background=TextBackground(color=FANCY_BG_COLOR, round_radius=0.12, alpha=1.0),
        clip_settings=ClipSettings(transform_y=FANCY_Y),
    )
    sf.add_segment(seg, track)


def add_hint(sf, text: str, start_us: int, dur_us: int, track: str = "提示轨"):
    """在时间轴上标注"这里该放什么音效/音乐"，剪完记得删掉这一轨。"""
    seg = TextSegment(
        text,
        Timerange(start_us, dur_us),
        style=TextStyle(size=HINT_SIZE, bold=True, color=HINT_COLOR, align=1),
        background=TextBackground(color=HINT_BG_COLOR, round_radius=0.1, alpha=0.92),
        clip_settings=ClipSettings(transform_y=HINT_Y),
    )
    sf.add_segment(seg, track)


# ============================================================
#  四、主流程
# ============================================================

def fix_meta(draft_name: str, duration_us: int) -> None:
    """库自带的 meta 模板里路径和名称是空的，新版剪映可能不认，这里补齐。"""
    draft_path = os.path.join(DRAFT_ROOT, draft_name)
    meta_path = os.path.join(draft_path, "draft_meta_info.json")
    if not os.path.exists(meta_path):
        return
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    meta["draft_fold_path"] = os.path.normpath(draft_path)
    meta["draft_root_path"] = os.path.normpath(DRAFT_ROOT)
    meta["draft_name"] = draft_name
    meta["tm_duration"] = duration_us
    if not meta.get("draft_id"):
        meta["draft_id"] = str(uuid.uuid4()).upper()

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


def build(video_path: str, srt_path: str | None, draft_name: str, layout: str = "auto"):
    if not os.path.exists(DRAFT_ROOT):
        raise SystemExit(f"找不到剪映草稿目录：\n  {DRAFT_ROOT}\n请在脚本顶部改 DRAFT_ROOT。")

    src_w, src_h, dur = probe_video(video_path)
    lay = apply_layout(layout, src_w, src_h)
    print(f"草稿目录：{DRAFT_ROOT}")
    print(f"源视频：{src_w}×{src_h}  {dur/1e6:.1f}s  →  画布 {CANVAS_W}×{CANVAS_H}（{lay} 版式）")
    folder = DraftFolder(DRAFT_ROOT)
    sf = folder.create_draft(draft_name, CANVAS_W, CANVAS_H, FPS, allow_replace=True)
    print(f"已创建草稿：{draft_name}")

    # 0) 模糊背景轨（blur 版式）—— 必须排在最底下，剪映里轨道顺序 = 图层上下顺序
    if USE_BG:
        sf.append_track(TrackSpec(TrackType.video, "背景模糊"))
        sf.add_segment(make_bg_segment(video_path, 0, dur), "背景模糊")
        print(f"  背景模糊：整段铺满 ×{BG_SCALE:.2f}，模糊 {BG_BLUR}"
              f"（不透明度 {BG_ALPHA*100:.0f}%）")

    # 1) 视频轨
    sf.append_track(TrackSpec(TrackType.video, "主视频"))
    main_seg = VideoSegment(video_path, trange(0, dur),
                            source_timerange=trange(0, dur),
                            clip_settings=video_clip())
    sf.add_segment(main_seg, "主视频")
    print(f"  视频轨：{os.path.basename(video_path)}（{dur/1e6:.1f}s"
          f"{f'，×{VIDEO_SCALE:.2f}' if VIDEO_SCALE != 1.0 else ''}）")

    # 2) 主字幕轨
    if srt_path and os.path.exists(srt_path):
        n = add_main_subtitles(sf, srt_path)
        print(f"  主字幕：{n} 条（已按单行 {SUB_MAX_LINE} 字断句）")
    else:
        sf.append_track(TrackSpec(TrackType.text, "主字幕"))
        print("  主字幕：空轨已建好，SRT 可用 --srt 传入")

    # 3) 花字轨
    sf.append_track(TrackSpec(TrackType.text, "花字"))
    print("  花字轨：已建好（≤5 个，只放爆点）")

    # 3.5) 标题轨（blur / split 版式才需要，放画面上方的空白区）
    if LAYOUT in ("split", "blur"):
        sf.append_track(TrackSpec(TrackType.text, "标题"))
        add_title(sf, "一句话标题（双击改）", 0, min(int(4e6), dur))
        print("  标题轨：已建好（改掉占位文字）")

    # 4) 提示轨 —— 只留一个标签：画面里叠多个橙色块会挡住人脸
    sf.append_track(TrackSpec(TrackType.text, "提示轨"))
    add_hint(sf, "【钩子】3 秒内必须出钩子", 0, min(int(1.2e6), dur))
    print("  提示轨：只留一个标签（导出前删掉这条轨）")

    sf.save()
    fix_meta(draft_name, dur)
    out = os.path.join(DRAFT_ROOT, draft_name, "draft_content.json")
    size_kb = os.path.getsize(out) / 1024
    print(f"\n✅ 已保存：{out}  ({size_kb:.0f} KB)")
    print("👉 现在打开剪映，草稿列表里就能看到它。")


def main():
    ap = argparse.ArgumentParser(description="生成剪映切片模板草稿")
    ap.add_argument("--video", help="素材视频路径")
    ap.add_argument("--srt", help="字幕 SRT 路径（可选）")
    ap.add_argument("--name", help="草稿名称")
    ap.add_argument("--demo", action="store_true", help="用内置测试视频生成演示草稿")
    ap.add_argument("--layout", default="auto", choices=["auto", "split", "fill"],
                    help="auto=按素材方向自动 | split=竖屏分栏 | fill=横屏满屏")
    args = ap.parse_args()

    if args.demo:
        video = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo", "demo.mp4")
        if not os.path.exists(video):
            raise SystemExit("找不到 demo/demo.mp4，请先运行生成测试视频的步骤。")
        srt = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo", "demo.srt")
        build(video, srt if os.path.exists(srt) else None, args.name or "切片模板-DEMO", args.layout)
        return

    if not args.video:
        ap.print_help()
        return
    if not os.path.exists(args.video):
        raise SystemExit(f"视频不存在：{args.video}")

    name = args.name or ("切片-" + os.path.splitext(os.path.basename(args.video))[0])
    build(args.video, args.srt, name, args.layout)


if __name__ == "__main__":
    main()
