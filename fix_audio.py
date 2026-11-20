# -*- coding: utf-8 -*-
"""
修复草稿「没有声音」的问题
================================================
症状：剪映里打开生成的草稿，画面正常、字幕正常，但完全没有声音。

原因：早期版本用「音量关键帧」(KFTypeVolume) 实现段首段尾渐入渐出。
      剪映把关键帧里的 0.0/1.0 当成 0%/1% 音量读，于是整段几乎是静音。

修法：删掉所有音量关键帧，改用剪映原生的「音量淡入淡出」(audio_fades)，
      音量恢复 100%。已有草稿不用重新挑句子，直接就地修复。

用法：
    python fix_audio.py                     # 修复最近一个草稿
    python fix_audio.py --draft 我的草稿名  # 修指定草稿
    python fix_audio.py --list              # 看有哪些草稿

说明：
    剪映 11.5 保存后 draft_content.json 是加密的，无法直接改。
    本脚本会自动回退到文件夹里 .backup/ 下的明文快照（剪映打开草稿时留下的），
    修好后以明文写回 draft_content.json —— 剪映下次打开会再自己加密，不影响使用。
"""

import argparse
import glob
import json
import os
import shutil
import sys
import time
import uuid

DRAFT_ROOT = os.path.expandvars(
    r"%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft"
)
FADE_US = 120_000          # 每段首尾淡化 120ms
MIN_SEG_US = 300_000       # 短于 0.3 秒的段不加淡化


def is_plain_json(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        return head.lstrip()[:1] in (b"{", b"[")
    except OSError:
        return False


def find_content(draft_dir: str):
    """找可读的 draft_content：优先明文正式文件，否则回退 .backup 里的明文快照。"""
    main = os.path.join(draft_dir, "draft_content.json")
    if os.path.isfile(main) and is_plain_json(main):
        return main
    cands = glob.glob(os.path.join(draft_dir, ".backup", "*.bak"))
    plain = [p for p in cands if is_plain_json(p)]
    if not plain:
        # 再退一步：根目录下的 .tmp 快照
        plain = [p for p in glob.glob(os.path.join(draft_dir, "*.tmp")) if is_plain_json(p)]
    if not plain:
        return None
    plain.sort(key=os.path.getmtime)
    return plain[-1]


def strip_volume_keyframes(doc) -> int:
    """删掉所有音量关键帧，返回删掉的列表数量。"""
    removed = 0
    for track in doc.get("tracks", []):
        for seg in track.get("segments", []):
            kfs = seg.get("common_keyframes") or []
            keep = [k for k in kfs if k.get("property_type") != "KFTypeVolume"]
            if len(keep) != len(kfs):
                removed += len(kfs) - len(keep)
                seg["common_keyframes"] = keep
            # 音量字段若被写成 0（旧草稿的另一种坏法），拉回 1.0
            if isinstance(seg.get("volume"), (int, float)) and seg["volume"] < 0.05:
                seg["volume"] = 1.0
                seg["last_nonzero_volume"] = 1.0
    return removed


def add_native_fades(doc) -> int:
    """给视频段加剪映原生淡入淡出，返回加了几段。"""
    mats = doc.setdefault("materials", {})
    fades = mats.setdefault("audio_fades", [])
    video_ids = {v.get("id") for v in mats.get("videos", []) or []}
    if not video_ids:
        video_ids = {v.get("material_id") for v in mats.get("videos", []) or []}

    n = 0
    for track in doc.get("tracks", []):
        if track.get("type") != "video":
            continue
        for seg in track.get("segments", []):
            if seg.get("material_id") not in video_ids:
                continue
            dur = (seg.get("target_timerange") or {}).get("duration", 0)
            if dur < MIN_SEG_US:
                continue
            f = min(FADE_US, int(dur * 0.15))
            if f <= 0:
                continue
            refs = seg.setdefault("extra_material_refs", [])
            # 已经挂过淡入淡出就不重复加
            exist = {x.get("id") for x in fades}
            if any(r in exist for r in refs):
                continue
            fid = uuid.uuid4().hex
            fades.append({
                "id": fid,
                "fade_in_duration": f,
                "fade_out_duration": f,
                "fade_type": 0,
                "type": "audio_fade",
            })
            refs.append(fid)
            n += 1
    return n


def list_drafts():
    if not os.path.isdir(DRAFT_ROOT):
        print(f"没找到剪映草稿目录：{DRAFT_ROOT}")
        return []
    out = []
    for name in os.listdir(DRAFT_ROOT):
        d = os.path.join(DRAFT_ROOT, name)
        if not os.path.isdir(d) or name.startswith("."):
            continue
        out.append((os.path.getmtime(d), name, d))
    out.sort(reverse=True)
    return out


def fix_one(draft_dir: str, name: str, dry: bool = False) -> bool:
    src = find_content(draft_dir)
    if not src:
        print(f"✗ {name}：找不到可读的草稿内容（可能被加密且没有明文快照）。\n"
              f"  这个草稿请用界面重新生成一次（新代码已经不会再写坏音量）。")
        return False

    doc = json.load(open(src, encoding="utf-8"))
    removed = strip_volume_keyframes(doc)
    added = add_native_fades(doc)

    total = sum(s.get("target_timerange", {}).get("duration", 0)
                for t in doc.get("tracks", []) if t.get("type") == "video"
                for s in t.get("segments", []))
    print(f"  来源：{os.path.relpath(src, draft_dir)}")
    print(f"  删掉音量关键帧：{removed} 组｜加原生淡入淡出：{added} 段｜视频总长 {total/1e6:.1f}s")

    if dry:
        print("  （试运行，未写入）")
        return True

    target = os.path.join(draft_dir, "draft_content.json")
    if is_plain_json(target):
        shutil.copy2(target, target + f".before_fix_{int(time.time())}")
    with open(target, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    # 剪映可能留下的加密备份也清掉，免得下次打开又读回旧内容
    for tmp in glob.glob(os.path.join(draft_dir, "template*.tmp")):
        try:
            os.remove(tmp)
        except OSError:
            pass
    print(f"  ✅ 已写回 {target}")
    return True


def main():
    ap = argparse.ArgumentParser(description="修复剪映草稿没有声音的问题")
    ap.add_argument("--draft", help="草稿名称（文件夹名），不填=最近一个")
    ap.add_argument("--list", action="store_true", help="只列草稿，不修")
    ap.add_argument("--dry", action="store_true", help="只看会怎么修，不写文件")
    args = ap.parse_args()

    drafts = list_drafts()
    if args.list:
        for _, name, d in drafts:
            print(f"  {name}   {time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(d)))}")
        return

    if args.draft:
        hit = [x for x in drafts if x[1] == args.draft]
        if not hit:
            print(f"没有叫「{args.draft}」的草稿。先跑 --list 看看有哪些：")
            for _, name, _d in drafts:
                print("   ", name)
            sys.exit(1)
        targets = hit
    else:
        if not drafts:
            print("草稿目录是空的。")
            return
        targets = drafts[:1]

    for _, name, d in targets:
        print(f"\n▶ {name}")
        fix_one(d, name, dry=args.dry)
    print("\n打开剪映，草稿列表里点开就能听到声音了。")


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    main()
