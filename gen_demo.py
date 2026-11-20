# -*- coding: utf-8 -*-
"""生成演示素材：一段 15 秒竖屏测试视频 + 配套 SRT 字幕。"""
import os
import subprocess

import imageio_ffmpeg

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "demo")
os.makedirs(DEMO, exist_ok=True)

mp4 = os.path.join(DEMO, "demo.mp4")
if not os.path.exists(mp4):
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ff, "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30",
        "-t", "15",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
        mp4,
    ]
    print("生成测试视频…")
    subprocess.run(cmd, check=True, capture_output=True)
print("视频:", mp4, os.path.getsize(mp4) // 1024, "KB")

srt = os.path.join(DEMO, "demo.srt")
lines = [
    (0.2, 2.8, "这波我必赢，稳得很"),
    (3.0, 6.0, "你看着啊，我先把兵线推过去，然后再绕后，这波他绝对想不到我要干什么"),
    (6.2, 8.0, "啊？"),
    (8.2, 10.5, "不是吧，他怎么会在这儿"),
    (10.7, 13.5, "我这个操作，我说实话，职业选手来了也得愣一下"),
    (13.7, 15.0, "算了不说了"),
]


def stamp(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


with open(srt, "w", encoding="utf-8") as f:
    for i, (a, b, t) in enumerate(lines, 1):
        f.write(f"{i}\n{stamp(a)} --> {stamp(b)}\n{t}\n\n")
print("字幕:", srt)
