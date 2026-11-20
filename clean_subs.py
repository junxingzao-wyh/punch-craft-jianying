"""抹掉画面里已经烧死的字幕，同时保持画面原始比例。

为什么不用「裁切」
    裁掉画面底部 18% 会把 16:9 变成 2.1:1，放进竖屏里显得又扁又矮，
    而且切口接模糊背景会有一条明显的拼贴缝。用户的诉求是「尺寸保持原样」。

做法是「原位抹除」
    先自动找到字幕所在的那条横带（白字 + 黑描边，通常在画面底部 82%–91%），
    再用 ffmpeg 的 delogo 滤镜把这块用四周像素插值「补」平。
    字幕区域基本都是衣服/桌面这类大片纯色，补完几乎看不出痕迹。
    （试过「把上方一条像素拉长盖上去」和「强模糊一条」，前者遇到手部动作会
      拉出残影，后者边界是硬方框 —— delogo 是三种里最干净的。）

代价
    视频需要重新编码一次（音频直接复制）。5 分钟 1080p 约 1 分钟，
    结果会缓存成 uploads/_clean/<原名>-clean.mp4，第二次用同一条片子不再重算。
"""

import os
import subprocess

import imageio_ffmpeg

import make_draft as md

CLEAN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads", "_clean")

# 检测不到字幕时的兜底区间（按这个素材实测出来的）
FALLBACK_BAND = (0.822, 0.900, 0.20, 0.80)


def _ffmpeg():
    return imageio_ffmpeg.get_ffmpeg_exe()


def _probe_size(video):
    w, h, _ = md.probe_video(video)
    return w, h


def _grab(video, t_sec, out_png):
    subprocess.run(
        [_ffmpeg(), "-y", "-ss", f"{t_sec:.2f}", "-i", video, "-frames:v", "1", out_png],
        capture_output=True,
    )
    return os.path.exists(out_png)


def detect_band(video, probe_n=10, log=print):
    """找出字幕带的 y 范围和 x 范围（都返回 0–1 的比例）。

    判据：同一行里既有很亮的像素（字的填充），又有很暗的像素（描边），
    而且这一行在画面下方 65% 以下。找到最靠下那一簇，取所有抽帧的并集。
    """
    from PIL import Image
    import numpy as np

    w, h = _probe_size(video)
    dur = md.video_duration_us(video) / 1e6
    if dur <= 2:
        return FALLBACK_BAND

    tmp = os.path.join(os.path.dirname(CLEAN_DIR), "_probe_frame.png")
    ys, xs = [], []
    hits = 0

    for i in range(probe_n):
        t = 1.0 + (dur - 2.0) * i / max(1, probe_n - 1)
        if not _grab(video, t, tmp):
            continue
        a = np.asarray(Image.open(tmp).convert("RGB")).astype(int)
        b = a.mean(2)
        rows = []
        for y in range(int(h * 0.65), h):
            row = b[y]
            if (row > 237).sum() >= 40 and (row < 70).sum() >= 4:
                rows.append(y)
        if not rows:
            continue

        # 取最靠下的连续簇（允许 8px 断裂）
        clusters, cur = [], [rows[0]]
        for y in rows[1:]:
            if y - cur[-1] <= 8:
                cur.append(y)
            else:
                clusters.append(cur)
                cur = [y]
        clusters.append(cur)
        band = clusters[-1]
        if len(band) < 18:            # 太薄，不像字幕，跳过
            continue

        y0, y1 = band[0], band[-1]
        seg = b[y0:y1 + 1]
        colmask = (seg > 237).sum(0)
        cxs = np.where(colmask >= 2)[0]
        if len(cxs) < 20:
            continue

        ys.append((y0 / h, y1 / h))
        xs.append((cxs.min() / w, cxs.max() / w))
        hits += 1

    if os.path.exists(tmp):
        os.remove(tmp)

    if hits < 3:
        log(f"  原字幕：只认出 {hits}/{probe_n} 帧，用兜底区间 {FALLBACK_BAND}")
        return FALLBACK_BAND

    y0 = min(v[0] for v in ys)
    y1 = max(v[1] for v in ys)
    x0 = min(v[0] for v in xs)
    x1 = max(v[1] for v in xs)

    # 上下各留一点余量（描边、句末标点会略超）
    y0 -= 0.012
    y1 += 0.014

    # 横向：字幕是居中的，但长短差很多（抽帧只能看到部分句子），
    # 所以以中线为轴对称放宽，最少覆盖画面 62% 宽，免得长句两头残留半个字。
    mid = (x0 + x1) / 2
    half = max((x1 - x0) / 2 + 0.07, 0.31)
    x0, x1 = mid - half, mid + half

    y0, y1 = max(0.0, y0), min(1.0, y1)
    x0, x1 = max(0.0, x0), min(1.0, x1)
    log(f"  原字幕：{hits}/{probe_n} 帧命中，纵向 {y0*100:.1f}%–{y1*100:.1f}%，"
        f"横向 {x0*100:.0f}%–{x1*100:.0f}%")
    return (y0, y1, x0, x1)


def clean_path(video):
    stem = os.path.splitext(os.path.basename(video))[0]
    return os.path.join(CLEAN_DIR, f"{stem}-clean.mp4")


def ensure_clean(video, log=print, force=False):
    """返回抹掉字幕后的视频路径（有缓存就直接用）。"""
    out = clean_path(video)
    if not force and os.path.exists(out) and \
            os.path.getmtime(out) >= os.path.getmtime(video):
        log(f"  原字幕：复用已处理文件 {os.path.basename(out)}")
        return out

    w, h = _probe_size(video)
    y0r, y1r, x0r, x1r = detect_band(video, log=log)

    # 矩形往四周各放宽 3px：delogo 会内缩 1px 采样，不扩会留一圈描边残影
    y0 = max(1, int(round(y0r * h)) - 3) // 2 * 2
    y1 = min(h - 2, int(round(y1r * h)) + 3)
    bh = max(24, (y1 - y0) // 2 * 2)                 # 目标带高
    cw = max(64, int(round((x1r - x0r) * w)) // 2 * 2)  # 处理宽度
    cx = max(1, int(round(x0r * w)) - 3) // 2 * 2
    if cx + cw > w - 2:
        cw = (w - 2 - cx) // 2 * 2

    # delogo：拿矩形四条边外的像素往里插值，等于把这块「补」成周围的样子。
    # 比「缩小一块再放大盖上去」稳得多 —— 后者遇到手/胳膊动起来会拉出残影。
    vf = f"delogo=x={cx}:y={y0}:w={cw}:h={bh}"

    os.makedirs(CLEAN_DIR, exist_ok=True)
    log(f"  原字幕：正在抹除（{w}x{h}，覆盖 x={cx} y={y0} {cw}x{bh}），需要一会儿…")
    r = subprocess.run(
        [_ffmpeg(), "-y", "-i", video, "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", out],
        capture_output=True,
    )
    if r.returncode != 0 or not os.path.exists(out):
        msg = r.stderr.decode("utf-8", "ignore")[-400:]
        raise SystemExit(f"抹字幕失败：{msg}")

    src_dur = md.video_duration_us(video)
    new_dur = md.video_duration_us(out)
    if abs(new_dur - src_dur) > 200_000:             # 差 0.2 秒以上，时间轴会错位
        log(f"  ⚠ 处理前后时长差 {(new_dur-src_dur)/1e6:.2f}s，请检查时间轴是否对齐")
    log(f"  原字幕：已抹除 → {os.path.basename(out)}"
        f"（{os.path.getsize(out)/1024/1024:.0f}MB，画面比例未变）")
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python clean_subs.py <视频路径>")
        print("  例: python clean_subs.py uploads/你的回放.mp4")
        raise SystemExit(1)
    p = sys.argv[1]
    print(detect_band(p))
    print(ensure_clean(p))
