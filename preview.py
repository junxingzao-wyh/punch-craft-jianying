# -*- coding: utf-8 -*-
"""
版面预览：把「视频 + 标题 + 字幕」按草稿里的位置合成一张图。
================================================
只用来核对版面（谁在哪儿、字挡不挡脸、两套字幕有没有撞），
不是剪映的最终渲染效果，字体粗细会有细微差别。

用法：
    python preview.py --video uploads/你的回放.mp4 --at 150 --sub "这一句字幕" --out preview.png
"""

import argparse
import os
import subprocess
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import make_draft as md

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]


def _font(px: int):
    from PIL import ImageFont
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, px)
            except Exception:
                continue
    return ImageFont.load_default()


def _grab_frame(video: str, t_sec: float) -> "Image.Image":
    from PIL import Image
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_preview_frame.png")
    subprocess.run([ff, "-y", "-ss", f"{max(0, t_sec):.2f}", "-i", video,
                    "-frames:v", "1", tmp, "-loglevel", "error"], check=True)
    img = Image.open(tmp).convert("RGB")
    try:
        os.remove(tmp)
    except Exception:
        pass
    return img


def render(video: str, at: float, sub_text: str = "", title: str = "",
           layout: str = "auto", out: str = "preview.png") -> str:
    """按草稿的真实位置画一张预览图。"""
    from PIL import Image, ImageDraw, ImageFilter, ImageOps

    src_w, src_h, _ = md.probe_video(video)
    lay = md.apply_layout(layout, src_w, src_h)
    W, H = md.CANVAS_W, md.CANVAS_H

    canvas = Image.new("RGB", (W, H), (10, 11, 15))
    frame = _grab_frame(video, at)

    def y_of(transform_y: float) -> int:
        return int(round((1 - transform_y) / 2 * H))

    # 素材「完整显示」时的尺寸（fit），剪映里 scale=1 就是这个大小
    if src_w / src_h >= W / H:
        base_w, base_h = W, int(round(W * src_h / src_w))
    else:
        base_w, base_h = int(round(H * src_w / src_h)), H

    # 背景层：同一帧 cover 铺满 + 高斯模糊 + 按 alpha 压暗到黑底
    # 模糊半径跟着 BG_BLUR 走（0.8 ≈ 0.036 画布宽），这样预览图看到的多糊，
    # 剪映里导出来就是多糊 —— 调小/调大不至于「预览没变、成片变了」。
    if md.USE_BG:
        bg = ImageOps.fit(frame, (W, H), method=Image.LANCZOS)   # 等价于放大后裁掉超出
        radius = W * 0.00045 * max(0.0, min(100.0, md.BG_BLUR))
        if radius > 0.4:
            bg = bg.filter(ImageFilter.GaussianBlur(radius=radius))
        bg = Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), bg, md.BG_ALPHA)
        canvas.paste(bg, (0, 0))

    # 前景层：按 VIDEO_SCALE 缩放，中心落在 VIDEO_Y 对应的位置
    # 原字幕是在视频文件里就抹掉的（clean_subs.py），这里不再裁画面
    fg_w = max(2, int(round(base_w * md.VIDEO_SCALE)))
    fg_h = max(2, int(round(base_h * md.VIDEO_SCALE)))
    fg = frame.resize((fg_w, fg_h), Image.LANCZOS)
    fx, fy = (W - fg_w) // 2, y_of(md.VIDEO_Y) - fg_h // 2
    canvas.paste(fg, (fx, fy))

    draw = ImageDraw.Draw(canvas)

    def draw_line(text: str, cy: int, px: int, fill, stroke_px: int, stroke_fill):
        f = _font(px)
        # 简单换行：超过画布 86% 宽就断行
        maxw = W * 0.86
        lines, cur = [], ""
        for ch in text:
            if draw.textlength(cur + ch, font=f) <= maxw:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        lh = int(px * 1.28)
        top = cy - lh * len(lines) // 2
        for i, ln in enumerate(lines):
            w = draw.textlength(ln, font=f)
            draw.text(((W - w) / 2, top + i * lh), ln, font=f, fill=fill,
                      stroke_width=stroke_px, stroke_fill=stroke_fill)

    if title:
        draw_line(title, y_of(md.TITLE_Y), 62, (255, 219, 77), 5, (0, 0, 0))
    if sub_text:
        draw_line(sub_text, y_of(md.SUB_Y), 72, (255, 255, 255), 6, (0, 0, 0))

    # 画面边界与字幕中线（帮助判断是否撞到原字幕）
    if lay in ("split", "blur"):
        draw.rectangle([fx, fy, fx + fg_w, fy + fg_h], outline=(70, 78, 96), width=2)
    if sub_text:
        draw.line([(0, y_of(md.SUB_Y)), (W, y_of(md.SUB_Y))], fill=(255, 80, 80), width=1)

    canvas.save(out, quality=92)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--at", type=float, default=5.0)
    ap.add_argument("--sub", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--layout", default="auto")
    ap.add_argument("--out", default="preview.png")
    a = ap.parse_args()
    p = render(a.video, a.at, a.sub, a.title, a.layout, a.out)
    print("已生成：", p)


if __name__ == "__main__":
    main()
