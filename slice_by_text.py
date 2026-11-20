# -*- coding: utf-8 -*-
"""
按文字选段，自动生成剪映草稿
================================================
核心能力是「读文字稿挑片段 → 自动切片」：
不用装 FunASR 和 torch，直接用剪映自带字幕识别导出的 SRT。

工作流：
  1. 录屏丢进剪映 → 文本 → 智能字幕 → 识别
  2. 右上角「导出」→ 字幕导出为 .srt
  3. python slice_by_text.py list --srt 字幕.srt        # 看带编号的字幕清单
  4. python slice_by_text.py build --video 录屏.mp4 --srt 字幕.srt --pick 18,19,20 --hook 25
  5. 打开剪映，草稿已经排好：视频按你选的顺序拼好、字幕已重定时、钩子已前置

参数说明：
  --pick    要保留的字幕编号，逗号分隔
  --hook    可选，把这一句提到最前面当「前置高潮」（填单个编号）
  --pad     只在整段选区的首尾各留几秒，默认 0.2
  --layout  auto / blur（竖屏+模糊背景，推荐） / split（竖屏分栏） / fill（跟素材走）
  --sub     加新字幕（默认不加 —— 原视频已经烧了字幕时，加了一定会打架）
  --fade    每段首尾音量渐入渐出，默认 0.12 秒（切点不"咔"的关键）
  --transition  段间转场，默认不加。叠化 / 闪白 / 闪黑 / 故障 / 模糊
"""

import argparse
import os
import sys

from pyJianYingDraft import (
    DraftFolder, KeyframeProperty, TextSegment, TrackSpec, TrackType,
    TransitionType, VideoSegment, trange,
)

import clean_subs
import make_draft as md
import markers
import suggest

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

MIN_SRC_US = 150_000        # 一句 SRT 最短按 0.15 秒算，防时间轴异常


def cmd_list(srt_path: str):
    entries = md.parse_srt(srt_path)
    if not entries:
        print("SRT 里没解析出内容，检查一下文件格式。")
        return
    print(f"{'编号':<5}{'起止时间':<24}文字")
    print("-" * 78)
    for i, (s, e, t) in enumerate(entries, 1):
        bar = f"{s/1e6:6.2f}s → {e/1e6:6.2f}s"
        print(f"{i:<5}{bar:<24}{t}")
    print("-" * 78)
    print(f"共 {len(entries)} 句。挑出你要的编号，例如：--pick 18,19,20")


def _cleanup_half_draft(draft_dir: str):
    """生成失败时把没写完的草稿目录删掉，免得剪映里多一个打不开的空项目。"""
    try:
        if os.path.isdir(draft_dir) and not os.path.exists(
                os.path.join(draft_dir, "draft_content.json")):
            import shutil
            shutil.rmtree(draft_dir, ignore_errors=True)
            print("  （已清理没生成完的草稿目录 —— 那个打不开的草稿被删掉了）")
    except Exception:
        pass


def group_picks(entries, idxs, pad_us: int):
    """把编号合并成「连续区间」，每段只切一刀。

    这是修掉「同一句播两遍」的关键：
    以前给每一句单独切一段、每段前后各留 0.2 秒，相邻两句就重叠 0.4 秒，
    听着就是同一句话说了两遍。现在连续挑中的句子合成一整段，中间不再切。
    """
    idxs = sorted(set(idxs))
    gap_us = max(2 * pad_us, 350_000)      # 间隔小于这个值就当成同一段，直接连起来
    groups, cur = [], None
    for i in idxs:
        s, e, t = entries[i - 1]
        e = max(e, s + MIN_SRC_US)
        if cur is not None and s - cur[1] <= gap_us:
            cur[1] = max(cur[1], e)
            cur[2].append((i, s, e, t))
        else:
            if cur is not None:
                groups.append(cur)
            cur = [s, e, [(i, s, e, t)]]
    if cur is not None:
        groups.append(cur)

    out = []
    for s, e, items in groups:
        out.append((max(0, s - pad_us), e + pad_us, items))
    return out


def cut_out(plan, holes):
    """从已有段落里把某些时间段挖掉（钩子句已经在开头播过一次，原位置不能再播）。"""
    holes = sorted(holes)
    if not holes:
        return plan
    out = []
    for s, e, items in plan:
        pieces = [(s, e)]
        for hs, he in holes:
            nxt = []
            for a, b in pieces:
                if he <= a or hs >= b:
                    nxt.append((a, b))
                    continue
                if hs > a:
                    nxt.append((a, hs))
                if he < b:
                    nxt.append((he, b))
            pieces = nxt
        for a, b in pieces:
            if b - a < 250_000:        # 剩下的碎渣不要
                continue
            sub = [it for it in items
                   if it[1] >= a - 1000 and it[2] <= b + 1000]
            if sub:
                out.append((a, b, sub))
    return out


def split_plan(plan, cuts):
    """在给定的源时间点上把段切开（总时长不变，只是多几刀）。

    切点用于「爆点前的留白」：要把 [爆点前 0.2s, 爆点句起点) 单独切成一小段，
    然后把这一小段的音量拉到 0 —— 画面照走、声音没了，这就是留白。
    """
    cuts = sorted(set(int(c) for c in cuts))
    out = []
    for s, e, items in plan:
        pts = [s] + [c for c in cuts if s < c < e] + [e]
        for a, b in zip(pts, pts[1:]):
            if b - a < 120_000:            # 切出来的碎渣并回前一块，别留 0.05 秒的闪帧
                if out and abs(out[-1][1] - a) < 1500:
                    out[-1][1] = b
                    out[-1][2] = out[-1][2] + [
                        it for it in items if it[1] >= a - 1000 and it[2] <= b + 1000]
                continue
            sub = [it for it in items if it[1] >= a - 1000 and it[2] <= b + 1000]
            out.append([a, b, sub])
    return out


def punch_layout(plan, entries, punch_ids, mute_us=220_000, min_mute_us=130_000):
    """为每个爆点句算出「留白区间」，返回 (新plan, 静音区间列表, 有效爆点句)。

    留白的起点取三者里最晚的那个，避免把上一句的尾音一起切静音：
        爆点句起点 - 220ms ／ 本段起点 ／ 上一句结尾 + 30ms
    """
    punches, mutes, cuts = [], [], []
    for idx in punch_ids:
        if idx < 1 or idx > len(entries):
            print(f"   [提示] 爆点句 {idx} 超出字幕范围，已忽略")
            continue
        s, e, _t = entries[idx - 1]
        e = max(e, s + MIN_SRC_US)

        piece = None
        for p in plan:
            if p[0] - 1000 <= s < p[1]:
                piece = p
                break
        if piece is None:
            print(f"   [提示] 爆点句 {idx} 不在你选的段里，已忽略")
            continue

        prev_end = piece[0]
        for (i, is_, ie, _txt) in piece[2]:
            if ie <= s and ie > prev_end:
                prev_end = ie
        a = max(s - mute_us, piece[0], prev_end + 30_000)
        if s - a >= min_mute_us:
            mutes.append((a, s))
            cuts += [a, s]
        else:
            print(f"   [提示] 爆点句 {idx} 前面没有空隙，这一处不做留白"
                  f"（想硬做就在剪映里手动切 0.2 秒并拉 0 音量）")
        punches.append(idx)
    if cuts:
        plan = split_plan(plan, cuts)
    return plan, mutes, punches


def cmd_build(video, srt_path, picks, hook, pad, name,
              layout="auto", sub=False, title=None, fade=0.12, transition="",
              punch="", cut_subs=None, hint_mode="marker", bg_blur=None):
    if cut_subs is not None:
        md.CUT_ORIG_SUBS = bool(cut_subs)
    # 背景模糊强度：对应剪映「模糊」特效那个滑杆（0–100，库内部再除以 100）。
    # 只作用在背景那一轨，前景主视频是干净的，糊不糊都不影响看清人脸。
    if bg_blur is not None and str(bg_blur) != "":
        md.BG_BLUR = min(100.0, max(0.0, float(bg_blur)))

    # 抹掉画面里烧死的原字幕：先出一份 -clean.mp4，之后所有轨道都用它。
    # 不裁画面 —— 裁了 16:9 会变 2.1:1，放进竖屏又扁又矮。
    src_video = video
    if md.CUT_ORIG_SUBS:
        video = clean_subs.ensure_clean(video)

    entries = md.parse_srt(srt_path)
    if not entries:
        raise SystemExit("SRT 解析为空")

    def grab(idx: int):
        if idx < 1 or idx > len(entries):
            raise SystemExit(f"编号 {idx} 超出范围（1–{len(entries)}）")
        return entries[idx - 1]

    if not picks and not hook:
        raise SystemExit("--pick 至少要给一个编号")

    src_w, src_h, src_dur = md.probe_video(video)
    lay = md.apply_layout(layout, src_w, src_h)

    pad_us = int(max(0.0, pad) * 1_000_000)

    # —— 排片：钩子单独一段（故意跳切），其余按连续区间合并 ——
    plan = []
    holes = []
    if hook:
        hs, he, ht = grab(hook)
        he = max(he, hs + MIN_SRC_US)
        hp = min(pad_us, 150_000)          # 钩子段的留白要短，不然会把上一句的尾巴带进来
        plan.append((max(0, hs - hp), he + hp, [(hook, hs, he, ht)]))
        holes.append((max(0, hs - hp), he + hp))
    rest = [i for i in picks if i != hook]
    plan += cut_out(group_picks(entries, rest, pad_us), holes)
    if not plan:
        raise SystemExit("没有任何有效选段")

    # —— 爆点句：前面切 0.2 秒做留白 + 画面推近（推近在下面视频轨建好后再加）——
    punch_ids = []
    for tok in str(punch or "").replace("，", ",").replace("、", ",").split(","):
        tok = tok.strip()
        if tok.isdigit():
            punch_ids.append(int(tok))
    mutes, punches = [], []
    if punch_ids:
        plan, mutes, punches = punch_layout(plan, entries, punch_ids)
        print(f"  爆点：{len(punches)} 处（第 {'、'.join(str(x) for x in punches)} 句）"
              f"，其中 {len(mutes)} 处带 0.2 秒静音留白")

    if not os.path.exists(md.DRAFT_ROOT):
        raise SystemExit(f"找不到剪映草稿目录：{md.DRAFT_ROOT}")
    folder = DraftFolder(md.DRAFT_ROOT)
    draft_dir = os.path.join(md.DRAFT_ROOT, name)
    sf = folder.create_draft(name, md.CANVAS_W, md.CANVAS_H, md.FPS, allow_replace=True)

    picked_sentences = sum(len(items) for _, _, items in plan)
    sub_pos = f"，字幕在画面 {_pct(md.SUB_Y):.0f}% 处" if sub else ""
    print(f"  源视频 {src_w}×{src_h} {src_dur/1e6:.1f}s → 画布 {md.CANVAS_W}×{md.CANVAS_H}"
          f"（{lay} 版式{sub_pos}）")
    print(f"  选中 {picked_sentences} 句，合并成 {len(plan)} 个视频段")
    if md.CUT_ORIG_SUBS:
        print(f"  原字幕：已抹除（用 delogo 补平画面底部那条带），画面比例保持 {src_w}×{src_h}")

    try:
        # —— 背景模糊轨：必须最先 append_track，剪映里轨道越靠前 = 图层越靠下 ——
        #   每一段都用和前景完全相同的源区间，这样跳切时背景跟着一起换画面，
        #   不会出现「前景跳到下一秒、背景还在原处」的错位感。
        #   音量必须给 0，否则同一段声音会播两遍（叠成回音）。
        if md.USE_BG:
            sf.append_track(TrackSpec(TrackType.video, "背景模糊"))
            print(f"  背景模糊：铺满 ×{md.BG_SCALE:.2f}，模糊 {md.BG_BLUR}"
                  f"，不透明度 {md.BG_ALPHA*100:.0f}%")

        # —— 视频轨：一个连续区间 = 一刀，段内不在中间重切，所以不会重复 ——
        sf.append_track(TrackSpec(TrackType.video, "主视频"))
        cursor = 0
        placed = []           # [(目标起点, 时长, [(编号, 相对起点, 时长, 文字)])]
        segs = []             # [(VideoSegment, 时长)]，用于接缝淡化/转场
        seg_meta = []         # [(VideoSegment, 时长, 源起点, 目标起点)]，用于定位爆点
        bg_segs = []          # 背景片段，等循环结束统一入库
        mute_targets = []     # 静音留白段在成片时间轴上的起点（给标记轨用）
        for src_start, src_end, items in plan:
            src_start = max(0, src_start)
            src_end = min(max(src_end, src_start + MIN_SRC_US), src_dur)
            dur = src_end - src_start
            is_mute = any(ms - 1500 <= src_start and src_end <= me + 1500 for ms, me in mutes)
            try:
                seg = VideoSegment(video, trange(cursor, dur),
                                   source_timerange=trange(src_start, dur),
                                   volume=0.0 if is_mute else 1.0,
                                   clip_settings=md.video_clip())
            except ValueError as e:
                raise SystemExit(
                    "取片段超出了视频时长 —— SRT 和视频可能不是同一段录屏。\n"
                    f"  出错位置：第 {len(placed)+1} 段，"
                    f"原片 {src_start/1e6:.1f}s–{src_end/1e6:.1f}s，视频总长 {src_dur/1e6:.1f}s\n"
                    f"  原始错误：{e}"
                )
            subs = []
            for (i, s, e, t) in items:
                subs.append((i, max(0, s - src_start), max(e - s, MIN_SRC_US), t))
            placed.append((cursor, dur, subs))
            segs.append((seg, dur))
            seg_meta.append((seg, dur, src_start, cursor))
            if is_mute and cursor not in mute_targets:
                mute_targets.append(cursor)
            if md.USE_BG:
                bg_segs.append(md.make_bg_segment(video, cursor, dur, src_start))
            cursor += dur
        print(f"  视频轨：{len(placed)} 段，共 {cursor/1e6:.1f} 秒")
        if bg_segs:
            for bg in bg_segs:
                sf.add_segment(bg, "背景模糊")

        # —— 接缝处理：段首段尾音量渐入渐出（消除爆音与突兀）+ 可选转场 ——
        #  经验：切点听着"生硬"，九成是声音突变，不是缺画面转场。
        #  所以淡化默认开、转场默认关；转场只在"时间跳跃"处用，满片叠化会像 PPT。
        #
        #  ⚠ 必须用 add_fade（剪映原生的「音量淡入淡出」，写进 materials.audio_fades），
        #    不要用音量关键帧（KFTypeVolume）：关键帧写法在部分剪映版本里会被读成
        #    "整段音量 0"，结果草稿完全没有声音。这个坑踩过，别再改回去。
        if fade and fade > 0:
            n_fade = 0
            for seg, dur in segs:
                if dur < 300_000 or seg.volume < 0.05:   # 留白段本来就静音，不用再淡
                    continue
                f = min(int(fade * 1_000_000), int(dur * 0.15))
                if f <= 0:
                    continue
                try:
                    seg.add_fade(f, f)
                except Exception:
                    continue
                n_fade += 1
            print(f"  接缝淡化：{n_fade} 段，首尾各 {fade*1000:.0f}ms 渐入渐出（剪映原生淡入淡出）")

        trans_name = (transition or "").strip()
        if trans_name and trans_name not in ("无", "none", "None"):
            try:
                tt = TransitionType.from_name(trans_name)
            except ValueError:
                raise SystemExit(
                    f"没有叫「{trans_name}」的转场。\n"
                    "  常用的有：叠化 / 闪白 / 闪黑 / 故障 / 模糊 / 雾化"
                )
            n_tr, last_ms = 0, 0
            for seg, dur in segs[:-1]:        # 转场挂在前一段的尾部
                if dur < 1_200_000:           # 段太短，转场会吃掉太多正文
                    continue
                last_ms = min(300, int(dur / 1e6 * 200))   # 毫秒，≤300
                seg.add_transition(tt, duration=last_ms * 1000)
                n_tr += 1
            print(f"  转场：{trans_name} × {n_tr} 处，每处 {last_ms}ms")

        # —— 爆点推近：0.14 秒推到 1.12 倍 → 维持 0.45 秒 → 0.45 秒回正 ——
        #  只动 scale_x / scale_y（等比，不变形）。不要用 uniform_scale 关键帧，
        #  那个在剪映里可能只缩 X 轴，画面会被横向拉扁。
        PUNCH_SCALE = 1.12
        n_push, punch_marks = 0, []
        for idx in punches:
            s = entries[idx - 1][0]
            hit = None
            for seg, dur, sseg, tseg in seg_meta:
                if sseg - 1000 <= s < sseg + dur:
                    hit = (seg, dur, s - sseg, tseg)
                    break
            if hit is None:
                continue
            seg, dur, rel, tseg = hit
            if dur < 500_000:
                continue
            rel = max(0, min(rel, dur - 1000))
            if rel >= 140_000:                   # 爆点在段中间：推之前先钉一帧 1.0
                frames = {rel - 140_000: 1.0, rel: PUNCH_SCALE}
                push_at = rel
            else:                                # 爆点就在段首：从 0 开始 0.16 秒推上去
                push_at = min(dur - 1000, 160_000)
                frames = {0: 1.0, push_at: PUNCH_SCALE}
            frames[min(dur - 1000, push_at + 450_000)] = PUNCH_SCALE
            if push_at + 900_000 <= dur:
                frames[push_at + 900_000] = 1.0
            for off in sorted(frames):
                seg.add_keyframe(KeyframeProperty.scale_x, off, frames[off])
                seg.add_keyframe(KeyframeProperty.scale_y, off, frames[off])
            n_push += 1
            punch_marks.append((idx, tseg + rel))
        if n_push:
            print(f"  爆点推近：{n_push} 处 ×{PUNCH_SCALE}（0.14s 推出 / 0.45s 维持 / 0.45s 回正）")

        # 接缝都处理好之后再入库 —— 库是在 add_segment 时才把转场素材收进 materials 的
        for seg, _ in segs:
            sf.add_segment(seg, "主视频")

        # —— 主字幕轨：一条 SRT 一句，时间按「原片时刻 - 本段起点」精确落位 ——
        if sub:
            sub_track = "主字幕"
            sf.append_track(TrackSpec(TrackType.text, sub_track))
            n_sub = 0
            for tgt_start, _, subs in placed:
                for i, rel, d, text in subs:
                    for line, off, ld in md.plan_subtitles(text, d):
                        sf.add_segment(
                            TextSegment(line, trange(tgt_start + rel + off, ld),
                                        style=md.main_subtitle_style(),
                                        border=md.MAIN_BORDER,
                                        clip_settings=md.main_clip()),
                            sub_track,
                        )
                        n_sub += 1
            print(f"  主字幕：{n_sub} 行（{picked_sentences} 句，单行 ≤{md.SUB_MAX_LINE} 字，"
                  f"与语音逐句对齐）")
        else:
            sf.append_track(TrackSpec(TrackType.text, "主字幕"))
            print("  主字幕：按设置跳过（原视频自带字幕时不重复加，避免两套字幕打架）")

        # —— 标题轨（blur / split 才需要，放在画面上方的空白区）——
        if lay in ("split", "blur"):
            sf.append_track(TrackSpec(TrackType.text, "标题"))
            t_txt = (title or "").strip() or "这里写一句标题，双击改"
            md.add_title(sf, t_txt, 0, min(int(4e6), cursor))
            print(f"  标题：{t_txt}")

        # —— 花字轨 ——
        sf.append_track(TrackSpec(TrackType.text, "花字"))
        print("  花字轨：已建好（等你加 3–5 个）")

        # —— 施工标记轨：把提示变成时间轴上的「占位音频」——
        #   提示写在画面里会挡脸（试过，最后只能留一个标签），写在网页清单里
        #   又得两个窗口来回对时间码。所以改成一条独立的静音音频轨：
        #   剪映时间轴上的音频片段会显示素材文件名，而文件名就是提示内容，
        #   于是一眼看到「12.4s_爆点-唱片刮擦」正好落在 12.4 秒。
        #   画面里什么都看不见；听到那里觉得该放音效，删掉占位、原位拖入真音效即可。
        marks = []
        if hook:
            marks.append(markers.Mark(0, "钩子-最炸这句放最前"))
        for idx, t in punch_marks:
            try:
                line = entries[idx - 1][2]
            except Exception:
                line = ""
            key, _label = suggest.detect_mood(line)
            # 判不出情绪（calm）就别瞎指定音效，改成把这句话的开头几个字写进去，
            # 这样时间轴上一眼知道是哪个爆点；该放什么音效看网页清单。
            if key == "calm":
                head = "".join(ch for ch in line if ch not in suggest.PUNCT)[:6]
                sfx = head or "自己挑音效"
            else:
                pair = suggest.SFX_BY_MOOD.get(key) or [("音效", "", "")]
                sfx = pair[0][0] or "音效"
            marks.append(markers.Mark(t, f"爆点-{sfx}"))
        for t in mute_targets:
            marks.append(markers.Mark(t, "留白-静音半秒"))
        if placed:
            last_start = placed[-1][0]
            if last_start > 900_000:
                marks.append(markers.Mark(max(0, last_start - 900_000), "收尾-留白淡出"))

        try:
            n_mark = markers.add_marker_track(sf, marks, cursor)
        except Exception as e:
            n_mark = 0
            print(f"  [警告] 施工标记轨没建起来（{e}），只能看网页清单了")
        if n_mark:
            print(f"  施工标记：{n_mark} 个占位音频，摆在「{markers.TRACK_NAME}」轨上"
                  f"（画面里看不见，导出前删掉整条轨）")

        # —— 画面内标签：会挡脸，默认不给；想要就选「标记轨+画面标签」——
        if hint_mode in ("all", "one"):
            sf.append_track(TrackSpec(TrackType.text, "提示轨"))
            hint_cursor = 0

            def place_hint(text, start, dur):
                nonlocal hint_cursor
                start = max(int(start), hint_cursor)
                dur = min(int(dur), cursor - start)
                if dur <= 0:
                    return
                md.add_hint(sf, text, start, dur)
                hint_cursor = start + dur

            txt = "【钩子】最炸这句放最前" if hook else "【开头】3 秒内出钩子"
            place_hint(txt, 0, min(int(1.2e6), cursor))
            print(f"  画面标签：「{txt}」1.2 秒（会盖住一点画面，导出前删掉这条轨）")

        sf.save()
    except BaseException:
        _cleanup_half_draft(draft_dir)
        raise

    md.fix_meta(name, cursor)
    out = os.path.join(draft_dir, "draft_content.json")
    _self_check(out)

    # —— 版面预览图：从源视频里抽一帧，按草稿的真实位置把标题和字幕画上去 ——
    prev_path = ""
    try:
        import preview as pv
        src_start, src_end, items = plan[0]
        at = (max(0, src_start) + min(src_end, src_dur)) / 2 / 1e6
        mid = (src_end - src_start) / 2
        sub_txt = ""
        for (i, s, e, t) in items:
            if (s - src_start) <= mid <= (e - src_start):
                sub_txt = t
        if not sub_txt and items:
            sub_txt = items[0][3]
        prev_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview")
        os.makedirs(prev_dir, exist_ok=True)
        prev_path = os.path.join(prev_dir, f"{name}.jpg")
        pv.render(video, at, sub_txt if sub else "", title or "", lay, prev_path)
        print(f"   版面预览：{prev_path}")
    except Exception as e:
        print(f"   [提示] 预览图没生成（不影响草稿）：{type(e).__name__}: {e}")

    # —— 配乐/音效/花字/标题建议清单：边剪边看的施工图 ——
    sug_path = ""
    try:
        import suggest as sg
        tl = []
        for tgt_start, _d, subs in placed:
            for (i, rel, _dd, txt) in subs:
                tl.append((tgt_start + rel, i, txt))
        prev_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview")
        os.makedirs(prev_dir, exist_ok=True)
        sug_path = os.path.join(prev_dir, f"{name}-配乐清单.html")
        sg.render_html(
            sg.build(timeline=tl, hook=hook, punches=punches, punch_times=punch_marks,
                     seg_starts=[x[0] for x in placed], total=cursor, name=name,
                     n_segs=len(placed)),
            sug_path)
        print(f"   配乐/花字/标题建议：{sug_path}")
    except Exception as e:
        print(f"   [提示] 建议清单没生成（不影响草稿）：{type(e).__name__}: {e}")

    print("\n✅ 草稿已保存")
    print(f"   文件夹：{draft_dir}")
    print(f"   工程文件：{out}")
    print(f"   总时长 {cursor/1e6:.1f} 秒  |  打开剪映，草稿列表里就有它")
    return {"draft_dir": draft_dir, "content": out, "duration": cursor,
            "segments": len(placed), "sentences": picked_sentences,
            "preview": prev_path, "suggest": sug_path}


def _pct(y):
    """transform_y（单位=半个画布高）→ 画面百分比位置。"""
    return (1 - y) / 2 * 100


def _self_check(content_path: str):
    """读回草稿，逐轨检查有没有时间重叠 —— 重叠就是"同一句播两遍"的来源。"""
    import json
    try:
        with open(content_path, encoding="utf-8") as f:
            c = json.load(f)
        bad = 0
        parts = []
        for t in c.get("tracks", []):
            segs = sorted(
                (s["target_timerange"]["start"],
                 s["target_timerange"]["start"] + s["target_timerange"]["duration"])
                for s in t.get("segments", [])
            )
            ov = sum(1 for a, b in zip(segs, segs[1:]) if b[0] < a[1] - 1)
            bad += ov
            if segs:
                parts.append(f"{t['type']}×{len(segs)}")
        flag = "通过" if bad == 0 else f"发现 {bad} 处重叠"
        print(f"  自检：{' / '.join(parts)}｜时间重叠 {flag}")
        if bad:
            print("  ⚠ 选段之间存在重叠，请把这段日志贴到 issue 里。")
    except Exception as e:
        print(f"  [警告] 自检失败：{e}")


def main():
    ap = argparse.ArgumentParser(description="按文字选段生成剪映草稿")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("list", help="列出带编号的字幕清单")
    p1.add_argument("--srt", required=True)

    p2 = sub.add_parser("build", help="按编号选段并生成草稿")
    p2.add_argument("--video", required=True)
    p2.add_argument("--srt", required=True)
    p2.add_argument("--pick", default="", help="编号，逗号分隔，如 18,19,20")
    p2.add_argument("--hook", type=int, help="把这一句提到开头当钩子")
    p2.add_argument("--pad", type=float, default=0.2, help="整段首尾的留白秒数")
    p2.add_argument("--keep-subs", dest="cut_subs", action="store_false",
                    help="保留画面里烧死的原字幕（默认会裁掉）")
    p2.add_argument("--hint", default="marker", choices=["marker", "all", "one"],
                    help="提示放哪：marker=时间轴占位音频（默认，画面干净）；"
                         "all=占位音频+画面标签；one=只放画面钩子标签（旧行为）")
    p2.add_argument("--layout", default="auto", choices=["auto", "blur", "split", "fill"],
                    help="blur=竖屏+模糊背景（发抖音默认）；split=竖屏分栏；fill=跟素材走")
    p2.add_argument("--sub", action="store_true", help="加新字幕（默认不加：原片已烧字幕时容易打架）")
    p2.add_argument("--no-sub", action="store_true", help="（保留参数，等同默认）")
    p2.add_argument("--fade", type=float, default=0.12,
                    help="每段首尾音量渐入渐出秒数，0 关闭（默认 0.12，消除切点爆音）")
    p2.add_argument("--transition", default="",
                    help="段间转场，默认不加。可选：叠化 / 闪白 / 闪黑 / 故障 / 模糊")
    p2.add_argument("--title", help="分栏版式下的标题文字")
    p2.add_argument("--punch", default="",
                    help="爆点句编号，逗号分隔（如 42,88）：自动做 0.2s 静音留白 + 画面推近 1.12 倍")
    p2.add_argument("--bg-blur", dest="bg_blur", type=float, default=None,
                    help="背景模糊强度 0–100（剪映「模糊」特效滑杆的量程）：0 不糊 / 35 轻（默认）"
                         " / 55 中 / 80 强 / 100 极强；只影响背景那一轨")
    p2.add_argument("--name", help="草稿名称")

    args = ap.parse_args()

    if args.cmd == "list":
        cmd_list(args.srt)
        return

    picks = [int(x) for x in args.pick.replace("，", ",").split(",") if x.strip()]
    name = args.name or ("切片-" + os.path.splitext(os.path.basename(args.video))[0])
    if not os.path.exists(args.video):
        raise SystemExit(f"视频不存在：{args.video}")
    cmd_build(args.video, args.srt, picks, args.hook, args.pad, name,
              layout=args.layout, sub=bool(args.sub and not args.no_sub),
              title=args.title, fade=args.fade, transition=args.transition,
              punch=getattr(args, "punch", ""),
              cut_subs=getattr(args, "cut_subs", True),
              hint_mode=getattr(args, "hint", "marker"),
              bg_blur=getattr(args, "bg_blur", None))


if __name__ == "__main__":
    main()
