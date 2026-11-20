# -*- coding: utf-8 -*-
"""施工标记（时间轴占位音）—— 把「第几秒该放什么」直接摆到剪映时间轴上
================================================
问题：提示如果写在画面里，会挡住人脸和字幕；如果只写在网页清单里，
      剪映时间轴上看不见，只能两个窗口来回对时间码。

做法：生成一串「静音占位音频」，文件名就是提示内容，按真实时间点摆到
      一条独立音频轨上。剪映时间轴上的音频片段会显示素材文件名，
      于是一眼就能看到「12.4s_爆点-刮擦」正好卡在 12.4 秒。

好处：
  · 画面里什么都看不见（静音、不占画面、不挡脸）
  · 位置精确到 0.1 秒，和成片时间轴完全对齐
  · 听到那里觉得该放音效，直接把占位删掉、在同一位置拖入真音效就行
  · 整条轨可以在导出前一次性删掉（轨名里已写明）

用法：
    from markers import add_marker_track
    add_marker_track(sf, [Mark(12_400_000, "爆点-刮擦"), ...], total_us=cursor)
"""

import os
import shutil
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MARKER_DIR = os.path.join(BASE_DIR, "uploads", "_markers")
TRACK_NAME = "施工标记·导出前删我"

_BAD = '<>:"/\\|?*'
_SILENCE = None


class Mark:
    """一个时间轴标记：at 是它在成片时间轴上的位置（微秒），label 是显示内容。"""

    __slots__ = ("at", "label")

    def __init__(self, at: int, label: str):
        self.at = int(at)
        self.label = label

    def __repr__(self):
        return f"Mark({self.at/1e6:.1f}s, {self.label!r})"


def _safe(text: str, limit: int = 18) -> str:
    out = "".join(ch for ch in (text or "") if ch not in _BAD)
    out = out.replace("，", "-").replace(",", "-")
    return out.strip()[:limit] or "提示"


def _ensure_silence() -> str:
    """生成 1 秒静音 wav 当底座（只做一次）。"""
    global _SILENCE
    if _SILENCE and os.path.exists(_SILENCE):
        return _SILENCE
    os.makedirs(MARKER_DIR, exist_ok=True)
    dst = os.path.join(MARKER_DIR, "_silence.wav")
    if os.path.exists(dst) and os.path.getsize(dst) > 1000:
        _SILENCE = dst
        return dst

    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ff, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
           "-t", "1.0", "-c:a", "pcm_s16le", dst, "-loglevel", "error"]
    subprocess.run(cmd, check=True, timeout=60)
    _SILENCE = dst
    return dst


def make(label: str, at_us: int) -> str:
    """按「时间_内容」造一个占位音频文件，返回路径。

    文件名里的时间就是它在时间轴上的位置，所以剪映时间轴上一看就知道在哪。
    """
    base = _ensure_silence()
    t = max(0, at_us) / 1e6
    stem = f"{t:.1f}s_{_safe(label)}"
    dst = os.path.join(MARKER_DIR, stem + ".wav")
    if not (os.path.exists(dst) and os.path.getsize(dst) > 1000):
        shutil.copyfile(base, dst)
    return dst


def add_marker_track(sf, marks, total_us: int, track_name: str = TRACK_NAME,
                     dur_us: int = 800_000):
    """把标记全部摆到一条独立音频轨上。返回实际落位的标记数。

    同一时刻只能有一个片段（同轨不允许重叠），所以后面的标记会顺延；
    顺延后如果已经超出片尾就直接丢掉。
    """
    from pyJianYingDraft import (AudioSegment, Timerange, TrackSpec, TrackType)

    if not marks:
        return 0

    sf.append_track(TrackSpec(TrackType.audio, track_name))
    cursor = 0
    placed = 0
    for m in sorted(marks, key=lambda x: x.at):
        start = max(int(m.at), cursor)
        if start >= total_us:
            break
        dur = min(dur_us, total_us - start)
        if dur < 200_000:
            break
        try:
            path = make(m.label, start)
        except Exception as e:
            print(f"  [警告] 标记「{m.label}」没造出来（{e}）")
            continue
        sf.add_segment(AudioSegment(path, Timerange(start, dur), volume=0.0), track_name)
        cursor = start + dur
        placed += 1
    return placed
