# -*- coding: utf-8 -*-
"""
直播切片向导 —— 一步一步问，你不用记任何命令
================================================
用法：
    双击「start-cli.bat」（推荐）
    或在本目录执行：.venv\\Scripts\\python.exe wizard.py
    也可以把视频直接拖到「start-cli.bat」图标上，省掉第一步
"""

import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from slice_by_text import cmd_build, cmd_list


def clean_path(raw: str) -> str:
    """去掉拖拽文件时带上的引号和首尾空格。"""
    return raw.strip().strip('"').strip("'").strip()


def ask_path(prompt: str, must_exist: bool = True) -> str:
    while True:
        raw = input(prompt).strip()
        path = clean_path(raw)
        if not path:
            print("  × 没有输入，重来一次。\n")
            continue
        if must_exist and not os.path.exists(path):
            print(f"  × 找不到这个文件：{path}")
            print("     提示：把文件直接拖到这个窗口里，路径会自动填上。\n")
            continue
        return path


def main():
    # 支持把文件拖到 .bat 图标上：拖 1 个当视频，拖 2 个依次当视频和字幕
    args = [clean_path(a) for a in sys.argv[1:] if clean_path(a)]

    print()
    print("=" * 66)
    print("  直播切片向导")
    print("=" * 66)
    print("  需要两样东西：")
    print("    1. 录屏视频（mp4）")
    print("    2. 字幕文件（srt）—— 在剪映里用「智能字幕」识别后导出")
    print("  提示：文件可以直接从文件夹拖到这个窗口，路径会自动填上。")
    print("=" * 66)
    print()

    video = args[0] if len(args) >= 1 else ask_path("① 把【视频文件】拖进来，回车：\n   > ")
    if len(args) >= 1:
        print(f"① 视频：{video}")
    print()
    srt = args[1] if len(args) >= 2 else ask_path("② 把【SRT 字幕文件】拖进来，回车：\n   > ")
    if len(args) >= 2:
        print(f"② 字幕：{srt}")

    print()
    print("-" * 66)
    print("  字幕清单（这就是你能挑的句子）")
    print("-" * 66)
    try:
        cmd_list(srt)
    except Exception as e:
        print(f"  × 读取字幕失败：{type(e).__name__}: {e}")
        input("\n按回车退出…")
        return 1

    print()
    print("挑段原则（来自手册）：")
    print("  · 一条切片只讲一件事，通常挑 10–25 句，成片 45–75 秒")
    print("  · 铺垫要短，爆点要留，反应别切早")
    print("  · 把最炸的那一句单独挑出来当「钩子」，它会排到最前面")
    print()

    while True:
        pick_raw = input("③ 要保留哪些编号？（逗号分隔，如 12,13,14）\n   > ").strip()
        pick_raw = pick_raw.replace("，", ",").replace(" ", ",")
        try:
            picks = [int(x) for x in pick_raw.split(",") if x.strip()]
            if picks:
                break
        except ValueError:
            pass
        print("  × 格式不对，只能填数字和逗号，例如 12,13,14\n")

    hook_raw = input("\n④ 哪一句当「钩子」放到最前面？（填编号，没有就按回车跳过）\n   > ").strip()
    hook = int(hook_raw) if hook_raw.isdigit() else None

    pad_raw = input("\n⑤ 每段前后多留几秒？（回车用默认 0.25，反应镜头多的填 0.5）\n   > ").strip()
    try:
        pad = float(pad_raw) if pad_raw else 0.25
    except ValueError:
        pad = 0.25

    default_name = "切片-" + os.path.splitext(os.path.basename(video))[0]
    name = input(f"\n⑥ 给这条草稿起个名字？（回车用：{default_name}）\n   > ").strip()
    name = name or default_name

    print()
    print("=" * 66)
    print("  开始生成…")
    print("=" * 66)
    try:
        cmd_build(video, srt, picks, hook, pad, name)
    except SystemExit as e:
        print(f"\n  × 生成中断：{e}")
        input("\n按回车退出…")
        return 1
    except Exception as e:
        print(f"\n  × 出错了：{type(e).__name__}: {e}")
        print("     把这个报错发给我，我来定位。")
        input("\n按回车退出…")
        return 1

    print()
    print("=" * 66)
    print("  下一步（在剪映里，只有 4 件事）")
    print("=" * 66)
    print("  1. 删掉「提示轨」（它标着哪里该放音效，是施工标记）")
    print("  2. 在标出的位置加音效：音频 → 音效 → 搜「刮擦」「乌鸦」「叮」")
    print("  3. 在「花字」轨加 3–5 个花字，只放爆点，别压脸")
    print("  4. 配 BGM（压到 -20dB 左右）→ 封面 → 导出")
    print()
    print("  如果草稿没出现在剪映里：关掉剪映重新打开一次。")
    print()
    input("按回车退出…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
