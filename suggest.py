# -*- coding: utf-8 -*-
"""
配乐 / 音效 / 花字 / 标题 建议清单
================================================
切片生成后，程序已经知道这些事：每一句在第几秒、哪句是钩子、哪几句是爆点、
整条多长、切了几刀。凭这些信息就能把「该配什么」写成一份清单：

  1. BGM 三套方案（曲风、剪映里搜什么、音量压到多少、爆点前怎么卡点）
  2. 音效时间轴（第几秒、放什么、搜什么词、音量多少）
  3. 花字表（第几秒、建议写什么字、用哪种样式）
  4. 标题 / 封面文案（5 条，直接复制）

它不会替你决定"哪句好笑"——那是你的耳朵。它做的是：把句子按情绪分个类，
然后套上这行当里被验证过的搭配，省掉你"该搜什么词"的空白期。
"""

import html
import os

PUNCT = "，。！？、…~～,.!?;；:： \t\u3000\"'“”‘’（）()【】[]"


def _plain(t: str) -> str:
    return "".join(ch for ch in (t or "") if ch not in PUNCT)


# —— 情绪识别：只认最典型的词，宁可判错也不硬凑 ——
MOODS = [
    ("proud", "得意", ("必赢", "稳了", "稳得", "拿捏", "小意思", "轻松", "我赢",
                     "包赢", "随便打", "没问题", "乱杀", "随便玩")),
    ("fail", "翻车", ("啊？", "不是", "完了", "崩了", "输了", "死了", "没了",
                    "我靠", "卧槽", "翻车", "离谱", "白给", "寄了")),
    ("wow", "惊讶", ("真的假的", "居然", "竟然", "不会吧", "我天", "哇", "没想到")),
    ("taunt", "嘲讽", ("菜", "笑死", "哈哈哈", "呵呵", "你行你上", "就这", "破防")),
    ("anger", "上头", ("烦", "气死", "凭什么", "别吵", "闭嘴", "服了")),
]


def detect_mood(text: str):
    t = _plain(text)
    for key, label, kws in MOODS:
        for kw in kws:
            if kw in t:
                return key, label
    return "calm", "铺垫"


# —— 每种情绪配什么音效（搜这些词，剪映音效库里都有）——
SFX_BY_MOOD = {
    "proud": [("钻石 闪光", "叮的一声，装杯专用", "小音量叠在人声下，别抢话"),
              ("硬币 掉落", "得意 / 数钱感", "比闪光照更「俗」，看内容选一个")],
    "fail": [("唱片 刮擦", "翻车、打脸的标准音", "和乌鸦叠着放，各 0.3 秒"),
             ("乌鸦 飞过", "尴尬、无语", "音量 −18dB，当背景"),
             ("duang", "被打击", "重低音，跟爆点一起落")],
    "wow": [("电流 滋啦", "突然的惊讶", "短促，0.3 秒内"),
            ("金属 撞击", "硬转折", "别和「叮」同时用")],
    "taunt": [("哄笑 人群", "嘲讽、集体笑", "压到 −20dB，只做氛围"),
              ("鼓掌 稀落", "反讽", "越稀落越好笑")],
    "anger": [("心跳 低频", "上头、压迫", "0.8 秒，逐步变快更好"),
              ("鼓点 重击", "情绪顶点", "只落一下，别连续")],
    "calm": [("咻 转场", "换镜头、跳时间", "≤0.4 秒，多了吵"),
             ("唰 whoosh", "快速带过", "配合加速用")],
}

# —— 每种情绪配什么花字 ——
FANCY_BY_MOOD = {
    "proud": ("黄底黑字（综艺金句体）", "#FFD84D 底 + 黑字", "画面中上，别压脸",
              "得意的话要「俗」一点，黄底最像弹幕"),
    "fail": ("白字黑描边 + 轻微抖动", "白色 + 6px 黑边", "爆点位置正上方",
             "翻车必须大、必须抖，字号占画面宽 1/3"),
    "wow": ("大字描边（不出底）", "白字红边", "画面中央",
            "惊讶用纯描边，不要底色，画面更干净"),
    "taunt": ("手写吐槽体", "白色细手写", "右下角，小一号",
              "旁白感的吐槽放角落，不抢主体"),
    "anger": ("红字 + 爆炸底纹", "红字黄底", "画面中央偏上",
              "只有真上火才用，用多了廉价"),
    "calm": ("白字黑描边（基础款）", "白色 + 5px 黑边", "画面下方，避开原字幕",
             "没有明显情绪就用最素的样式，纯粹帮观众抓住这句话"),
}

# —— BGM 三套方案 ——
BGM_PLANS = [
    {
        "tag": "主推",
        "name": "轻快搞笑（口播最稳）",
        "search": "轻快 搞笑 / 滑稽 小号 / 喜剧 拨弦",
        "bpm": "100–120",
        "vol": "−20 ~ −24dB（人声占绝对主导）",
        "why": "口播切片的信息全在人声上，音乐只需要「不空」，一旦超过 −18dB 就开始糊。",
        "when": "内容以吐槽、日常、闲聊为主",
    },
    {
        "tag": "备选",
        "name": "鼓点紧张（对战 / 操作）",
        "search": "悬疑 紧张 / 电子 鼓点 / 卡点 节奏",
        "bpm": "120–130",
        "vol": "−22 ~ −26dB（鼓点容易盖人声）",
        "why": "有明确对抗、操作、倒计时的时候用，鼓点踩在动作上，观众会跟着紧张。",
        "when": "有游戏操作、对线、最后翻盘",
    },
    {
        "tag": "反转",
        "name": "前轻快 → 后悲情（音乐反转）",
        "search": "前段：轻快 搞笑；结尾：悲伤 小提琴 / 悲情 钢琴",
        "bpm": "前 110 / 后 70",
        "vol": "切换点前 0.5 秒把前一首拉 0，别做淡出",
        "why": "同一件事换个情绪配乐，荒诞感直接翻倍——这是「音乐反转」里最省力的一招。",
        "when": "结尾是惨败 / 打脸 / 自我打脸",
    },
]

REVERSE_TRICKS = [
    ("骤停", "爆点前 0.5 秒把 BGM 音量直接拉到 0（不是淡出），留 0.3 秒纯人声 → 再落音效。"),
    ("换风格", "同一段画面，音乐从欢快切成悲伤小提琴，观众会重新理解前面的内容。"),
    ("卡点", "把切点、花字弹出、音效全部对到鼓点上；短视频里最抓人的是「三连同步」。"),
    ("收尾", "最后一帧加一个上扬音阶或「叮」，比自然结束的留存率高。"),
]

TITLE_TEMPLATES = [
    "「{hook}」——说完这句他自己都笑了",
    "{hook}，结果下一秒就……",
    "主播最离谱的一次：{punch}",
    "三分钟前：{proud}；三分钟后：{fail}",
    "「{punch}」——这波真的绝了",
]


def _fmt(us: int) -> str:
    us = max(0, int(us))
    s = us / 1_000_000
    return f"{int(s // 60):02d}:{s % 60:04.1f}"


def funny_phrase(text: str, limit: int = 12) -> str:
    """从句子里挑一段最适合当花字的短句：优先强情绪词开头，否则取句尾。"""
    t = _plain(text)
    if not t:
        return ""
    for key, _label, kws in MOODS:
        for kw in kws:
            i = t.find(kw)
            if i >= 0 and len(t) - i >= 2:
                return t[i:i + limit]
    return t[-limit:] if len(t) > limit else t


def _score(text: str, is_punch: bool, is_hook: bool) -> int:
    key, _ = detect_mood(text)
    s = 0
    if key != "calm":
        s += 2
    if is_punch:
        s += 4
    if is_hook:
        s += 3
    n = len(_plain(text))
    if 4 <= n <= 14:
        s += 2
    raw = text or ""
    if any(ch in raw for ch in "？?！!"):
        s += 2
    if any(w in _plain(raw) for w in ("啊", "呢", "吧", "嘛", "呀", "呗")):
        s += 1
    if any(w in _plain(raw) for w in ("绝对", "直接", "根本", "居然", "竟然", "凭什么",
                                      "就不是", "我必", "这才", "完了", "白给", "你懂")):
        s += 2
    return s


def build(*, timeline, hook, punches, punch_times, seg_starts, total, name,
          n_segs, has_hook_text=""):
    """timeline: [(目标微秒, 编号, 文本)]；punch_times: [(编号, 目标微秒)]"""
    texts = {i: t for _t, i, t in timeline}
    times = {i: t for t, i, _x in timeline}
    hook_text = texts.get(hook, "") if hook else (has_hook_text or "")
    punch_ids = set(punches or [])
    punch_t = dict(punch_times or [])

    # —— 1. 音效时间轴 ——
    sfx = []
    if hook:
        sfx.append({"t": 0, "clock": "00:00.0", "where": "钩子第一句",
                    "what": "「咚」低音轰鸣 或 「叮」", "search": "低音 轰鸣 / Boom / 叮",
                    "vol": "−8dB", "note": "钩子要在 0.3 秒内给个声音标点，观众才不划走"})
    for idx in sorted(punch_ids):
        t = punch_t.get(idx)
        if t is None:
            continue
        sfx.append({"t": t, "clock": _fmt(t), "where": f"第 {idx} 句爆点",
                    "what": "低音轰鸣 Boom", "search": "低音 轰鸣 / Boom / 心跳",
                    "vol": "−6dB",
                    "note": "和画面推近同一帧落；前面 0.2 秒是静音，这一下才有对比"})
        sfx.append({"t": t + 350_000, "clock": _fmt(t + 350_000), "where": "爆点后半拍",
                    "what": "可叠「叮」/ 「唰」收尾", "search": "叮 / whoosh / 唰",
                    "vol": "−14dB", "note": "可选，只叠一下，别连成一串"})
    for k, s0 in enumerate(seg_starts):
        if k == 0 or s0 < 800_000:
            continue
        if len([x for x in sfx if abs(x["t"] - s0) < 500_000]) > 0:
            continue
        if [x for x in sfx if x["what"].startswith("「咻」")].__len__() >= 3:
            break
        sfx.append({"t": s0, "clock": _fmt(s0), "where": f"第 {k+1} 段切点",
                    "what": "「咻」转场音", "search": "咻 / whoosh / 转场",
                    "vol": "−12dB", "note": "切点有「咔」感时才加，太密反而吵"})
    sfx.append({"t": max(0, total - 500_000), "clock": _fmt(max(0, total - 500_000)), "where": "结尾",
                "what": "上扬音阶 / 「叮」", "search": "上扬 / 收尾 叮",
                "vol": "−10dB", "note": "最后半秒给个结束感，不留「话说到一半就没了」"})
    sfx.sort(key=lambda x: x["t"])

    # —— 2. 花字表：钩子 + 爆点 + 情绪最强的几句，最多 4 条 ——
    scored = []
    for t, i, txt in timeline:
        base = _score(txt, i in punch_ids, bool(hook) and i == hook)
        if total and t / total > 0.6:        # 笑点多半在后半段
            base += 1
        scored.append((base, t, i, txt))
    scored.sort(key=lambda x: -x[0])
    picks, seen = [], set()
    for _s, t, i, txt in scored:
        if i in seen or _s < 4:
            continue
        if any(abs(t - p[0]) < 3_000_000 for p in picks):   # 花字别挤在一起
            continue
        seen.add(i)
        picks.append((t, i, txt))
        if len(picks) >= 4:
            break
    picks.sort(key=lambda x: x[0])

    fancy = []
    for t, i, txt in picks:
        key, label = detect_mood(txt)
        style, color, pos, why = FANCY_BY_MOOD[key]
        fancy.append({
            "t": t, "at": _fmt(t), "idx": i,
            "words": funny_phrase(txt, 10),
            "origin": txt,
            "mood": label,
            "style": style, "color": color, "pos": pos, "why": why,
        })

    # —— 3. 标题 / 封面 ——
    punc_phrase = ""
    for idx in sorted(punch_ids):
        if texts.get(idx):
            punc_phrase = funny_phrase(texts[idx], 10)
            break
    if not punc_phrase and timeline:
        punc_phrase = funny_phrase(timeline[-1][2], 10)
    hook_phrase = funny_phrase(hook_text, 12) or (punc_phrase or "这波操作")
    proud = next((funny_phrase(texts[i], 8) for i in sorted(punch_ids)
                  if detect_mood(texts.get(i, ""))[0] == "proud"), "稳得很")
    fail = next((funny_phrase(texts[i], 8) for i in sorted(punch_ids)
                 if detect_mood(texts.get(i, ""))[0] == "fail"), "直接寄了")
    titles, _seen_t = [], set()
    for tpl in TITLE_TEMPLATES:
        t = tpl.format(hook=hook_phrase, punch=punc_phrase or hook_phrase,
                       proud=proud, fail=fail)
        if t in _seen_t:
            continue
        _seen_t.add(t)
        titles.append(t)

    cover = (punc_phrase or hook_phrase)
    if len(cover) > 10:                      # 先按标点切，取最长的一段，再不行砍后面
        segs = [x for x in cover.replace("，", ",").split(",") if len(x) >= 4]
        cover = (max(segs, key=len) if segs else cover)[:10]

    hook_big = funny_phrase(hook_text, 10) if hook_text else ""
    hook_bigs = [x for x in (hook_big, punc_phrase) if x]
    hook_bigs = list(dict.fromkeys(hook_bigs))[:2]

    return {
        "name": name, "total": total, "n_segs": n_segs, "hook_bigs": hook_bigs,
        "hook_text": hook_text, "punch_n": len(punch_ids),
        "sfx": sfx, "fancy": fancy, "titles": titles, "cover": cover,
        "punch_phrase": punc_phrase, "hook_phrase": hook_phrase,
        "dur_s": total / 1_000_000,
    }


CSS = """
:root{--ink:#1f1d1a;--ink2:#4b4741;--ink3:#807a71;--line:#e6e2d9;--bg:#fbfaf7;
--card:#fff;--acc:#c4552d;--acc2:#fdf3ee;--ok:#2f6b4f;--ok2:#eff6f1}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px 60px;background:var(--bg);color:var(--ink);
font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:24px;margin:0 0 6px;letter-spacing:-.3px}
.sub{color:var(--ink3);font-size:13px;margin-bottom:26px}
h2{font-size:18px;margin:34px 0 12px;padding-top:18px;border-top:1px solid var(--line)}
h2 span{display:inline-block;width:24px;height:24px;line-height:24px;text-align:center;
background:var(--acc);color:#fff;border-radius:6px;font-size:13px;margin-right:8px}
h3{font-size:15px;margin:18px 0 6px}
p{margin:8px 0;color:var(--ink2)}
small{color:var(--ink3)}
table{width:100%;border-collapse:collapse;margin:10px 0;font-size:13.5px}
th{text-align:left;font-weight:600;color:var(--ink3);font-size:12px;
border-bottom:1px solid var(--line);padding:6px 8px}
td{padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top}
td.t{font-family:ui-monospace,Consolas,monospace;color:var(--acc);white-space:nowrap}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:20px;
background:var(--acc2);color:var(--acc);margin-right:4px}
.tag.ok{background:var(--ok2);color:var(--ok)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:16px 18px;margin:12px 0}
.card.main{border-color:#e8bda9;box-shadow:0 1px 0 #f0e3db}
.card h3{margin-top:0}
.kv{display:grid;grid-template-columns:90px 1fr;gap:4px 12px;font-size:13.5px}
.kv b{color:var(--ink3);font-weight:500}
.copy{background:#f7f5f0;border:1px solid var(--line);border-radius:8px;
padding:9px 12px;margin:6px 0;font-size:14px;user-select:all}
.big{font-size:30px;font-weight:600;letter-spacing:2px}
.warn{background:#fdf6f3;border-left:3px solid var(--acc);padding:10px 14px;
border-radius:0 8px 8px 0;margin:14px 0;font-size:13.5px;color:#7a3b1f}
.words{display:inline-block;background:var(--acc2);border:1px solid #f0cfc0;
border-radius:6px;padding:1px 8px;font-weight:600;color:#8f3c1a}
"""


def render_html(d, path):
    e = html.escape
    parts = [f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(d['name'])} · 配乐与花字建议</title><style>{CSS}</style></head><body><div class="wrap">
<h1>配乐 / 音效 / 花字 / 标题 建议</h1>
<div class="sub">草稿「{e(d['name'])}」 · 全长 {d['dur_s']:.1f} 秒 · {d['n_segs']} 段 · 爆点 {d['punch_n']} 处　
<small>这是建议不是命令：音色不合口味就换，位置和时间点照抄就行。</small></div>"""]

    # —— 1 BGM ——
    parts.append('<h2><span>1</span>BGM：先选一套，再谈音效</h2>')
    parts.append("<p>口播切片最容易犯的错是 BGM 太响——观众听不清话，划走。"
                 "<b>人声是主角，音乐只是不让人尴尬。</b>下面三套按内容选一套：</p>")
    for p in BGM_PLANS:
        cls = "card main" if p["tag"] == "主推" else "card"
        parts.append(f"""<div class="{cls}"><h3><span class="tag{' ok' if p['tag']=='主推' else ''}">{e(p['tag'])}</span>{e(p['name'])}</h3>
<div class="kv"><b>剪映搜</b><div>{e(p['search'])}</div>
<b>速度</b><div>{e(p['bpm'])} BPM</div>
<b>音量</b><div>{e(p['vol'])}</div>
<b>适合</b><div>{e(p['when'])}</div></div>
<p style="margin-bottom:0"><small>{e(p['why'])}</small></p></div>""")
    parts.append("<h3>音乐反转四招（挑 1–2 招就够，别全用）</h3><table>")
    for k, v in REVERSE_TRICKS:
        parts.append(f"<tr><td style='width:70px'><b>{e(k)}</b></td><td>{e(v)}</td></tr>")
    parts.append("</table>")
    parts.append('<div class="warn"><b>版权：</b>剪映「音频 → 音乐素材」里带「可商用」标的最稳；'
                 '要发抖音就用抖音自己的曲库（在剪映里登录同一账号能看到）。'
                 '别用网易云下载的歌，容易被限流。</div>')

    # —— 2 音效 ——
    parts.append('<h2><span>2</span>音效：按时间点落，一共 {} 个</h2>'.format(len(d["sfx"])))
    parts.append("<p>剪映：顶部 <b>音频</b> → 左侧 <b>音效素材</b> → 搜下面的词 → 试听 → 点 <b>+</b> 上时间轴 → 把音量改成表里的值。</p>")
    parts.append("<table><tr><th style='width:74px'>时间</th><th>放什么</th>"
                 "<th style='width:150px'>搜这些词</th><th style='width:64px'>音量</th></tr>")
    for s in d["sfx"]:
        parts.append(f"<tr><td class='t'>{e(s['clock'])}</td>"
                     f"<td><small style='color:var(--acc)'>{e(s['where'])}</small><br>"
                     f"<b>{e(s['what'])}</b><br><small>{e(s['note'])}</small></td>"
                     f"<td><small>{e(s['search'])}</small></td><td>{e(s['vol'])}</td></tr>")
    parts.append("</table>")

    # —— 3 花字 ——
    parts.append('<h2><span>3</span>花字：{} 处，写什么已经帮你挑好</h2>'.format(len(d["fancy"])))
    parts.append("<p>剪映：顶部 <b>文本</b> → <b>花字</b> → 选风格 → 填字 → 拖到时间点。"
                 "字不用你编，直接抄下面「建议文字」（都从原话里截的，观众一看就懂）。</p>")
    if not d["punch_n"]:
        parts.append('<div class="warn">这次没填「爆点句号」，所以花字位置是按句子长度、'
                     '语气和位置挑的，<b>可能跟你感觉不一样</b>。下次生成时把最炸的几句编号填进去，'
                     '花字会直接锁在爆点上，音效表也会多出爆点那几条。</div>')
    parts.append("<table><tr><th style='width:70px'>时间</th><th style='width:170px'>建议文字</th>"
                 "<th>样式</th><th style='width:120px'>位置</th></tr>")
    for f in d["fancy"]:
        parts.append(
            f"<tr><td class='t'>{e(f['at'])}</td>"
            f"<td><span class='words'>{e(f['words'])}</span><br>"
            f"<small>原话：{e(f['origin'][:26])}</small></td>"
            f"<td><b>{e(f['style'])}</b><br><small>{e(f['color'])}｜{e(f['why'])}</small></td>"
            f"<td><small>{e(f['pos'])}</small></td></tr>")
    parts.append("</table>")
    parts.append('<div class="warn"><b>一条片子花字别超过 5 个。</b>每个花字停留 1–1.5 秒就够，'
                 '长过 2 秒观众会去读字、不听人说话。</div>')

    # —— 4 标题 ——
    parts.append('<h2><span>4</span>标题 / 封面：直接复制</h2>')
    parts.append("<p>发布标题用「具体的一句话」，别写「分享一个搞笑片段」这种没人点的。五条挑一条：</p>")
    for t in d["titles"]:
        parts.append(f'<div class="copy">{e(t)}</div>')
    if d.get("hook_bigs"):
        parts.append("<h3>开头 3 秒的钩子大字（打在画面下方，第 3 秒消失）</h3>")
        parts.append("<p><small>作用是让划到的人在半秒内知道「这条有戏」。用花字的大字样式，"
                     "白色或黄底都行，别盖住原字幕。</small></p>")
        for t in d["hook_bigs"]:
            parts.append(f'<div class="copy"><b>{e(t)}</b></div>')
    parts.append(f"<h3>封面大字（8–10 字，占画面 1/3）</h3>"
                 f'<div class="card"><span class="big">{e(d["cover"])}</span></div>')
    parts.append("<p><small>封面用原片抽一帧 + 一行大字就行，别加滤镜别加边框；"
                 "在剪映里用「文本」打上去，导出前记得把封面帧单独截出来。</small></p>")

    parts.append('<div class="warn" style="margin-top:28px"><b>别忘了最后一步：</b>'
                 '照着草稿里「施工标记」轨上那串占位音频加音效（文件名就是提示），加完整条删掉，再导出。</div>')
    parts.append("</div></body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return path
