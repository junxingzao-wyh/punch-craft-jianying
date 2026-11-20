# -*- coding: utf-8 -*-
"""
直播切片助手 · 本地网页界面
================================================
双击「start.bat」→ 浏览器自动打开 → 拖文件、点句子、生成草稿。
不联网、不上传云端，只在你本机 127.0.0.1 上跑。
"""

import contextlib
import io
import json
import os
import re
import socket
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import make_draft as md
from slice_by_text import cmd_build

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
LOG_PATH = os.path.join(UPLOAD_DIR, "_server.log")


def log(msg: str):
    """把每个请求记到 uploads/_server.log，出问题时有据可查。"""
    try:
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


PORT_RANGE = [8765, 8766, 8767, 8768, 8769]


def safe_name(name: str) -> str:
    """只留文件名，去掉 Windows 非法字符，防止路径穿越。"""
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r'[<>:"/|?*\x00-\x1f]', "_", name)
    return name.strip() or "unnamed"


def safe_draft_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "").strip()
    return name or "切片-未命名"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    # ---------- 基础输出 ----------
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _read_body(self) -> bytes:
        return b"".join(self._body_chunks())

    def _body_chunks(self):
        """兼容三种情况：正常 Content-Length、分块传输、没有长度头。"""
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    break
                try:
                    size = int(line.split(b";")[0], 16)
                except ValueError:
                    break
                if size == 0:
                    while True:
                        trailer = self.rfile.readline()
                        if trailer in (b"\r\n", b"\n", b""):
                            break
                    break
                left = size
                while left > 0:
                    b = self.rfile.read(min(1 << 20, left))
                    if not b:
                        return
                    left -= len(b)
                    yield b
                self.rfile.read(2)
            return

        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            return
        n = int(raw_len)
        got = 0
        while got < n:
            b = self.rfile.read(min(1 << 20, n - got))
            if not b:
                break
            got += len(b)
            yield b

    # ---------- GET ----------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/selftest":
            self._send(200, SELFTEST_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/health":
            self._json({"ok": True, "draft_root": md.DRAFT_ROOT})
        elif path.startswith("/preview/"):
            name = safe_name(urllib.parse.unquote(path[len("/preview/"):]))
            fp = os.path.join(BASE_DIR, "preview", name)
            if os.path.exists(fp):
                mime = "text/html; charset=utf-8" if name.lower().endswith(".html") else "image/jpeg"
                with open(fp, "rb") as f:
                    self._send(200, f.read(), mime)
            else:
                self._json({"ok": False, "error": "no preview"}, 404)
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    # ---------- POST ----------
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        log(f"POST {u.path}{('?' + u.query[:120]) if u.query else ''} | "
            f"CL={self.headers.get('Content-Length')} TE={self.headers.get('Transfer-Encoding')} "
            f"CT={self.headers.get('Content-Type')}")
        try:
            if u.path == "/api/upload":
                return self._upload(u)
            if u.path == "/api/list":
                return self._list()
            if u.path == "/api/build":
                return self._build()
            return self._json({"ok": False, "error": "not found"}, 404)
        except Exception as e:
            import traceback
            log("EXCEPTION " + traceback.format_exc().replace("\n", " | "))
            return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

    def _upload(self, u):
        q = urllib.parse.parse_qs(u.query)
        raw_name = q.get("name", ["upload.bin"])[0]
        name = safe_name(raw_name)
        kind = q.get("kind", ["file"])[0]
        dest = os.path.join(UPLOAD_DIR, name)
        got = 0
        try:
            with open(dest, "wb") as f:
                for chunk in self._body_chunks():
                    f.write(chunk)
                    got += len(chunk)
        except Exception as e:
            log(f"UPLOAD FAIL {name}: {type(e).__name__}: {e}")
            return self._json({"ok": False, "error": f"写文件失败：{e}"}, 500)

        log(f"UPLOAD {kind} name={raw_name!r} -> {dest} bytes={got}")
        if got == 0:
            return self._json({
                "ok": False,
                "error": "收到 0 字节 —— 服务可能被关掉了，或浏览器没把文件内容发出来。"
                         "请重新双击「start.bat」，并确认那个黑窗口没关。"
            }, 400)
        self._json({"ok": True, "path": dest, "kind": kind, "size": got})

    def _list(self):
        data = json.loads(self._read_body() or b"{}")
        srt = data.get("srt") or ""
        if not os.path.exists(srt):
            return self._json({"ok": False, "error": "找不到字幕文件"})
        entries = md.parse_srt(srt)
        rows = [{
            "i": i,
            "start": round(s / 1e6, 2),
            "end": round(e / 1e6, 2),
            "text": t,
        } for i, (s, e, t) in enumerate(entries, 1)]
        self._json({"ok": True, "entries": rows})

    def _build(self):
        data = json.loads(self._read_body() or b"{}")
        video = data.get("video") or ""
        srt = data.get("srt") or ""
        picks = [int(x) for x in (data.get("picks") or [])]
        hook = data.get("hook") or None
        pad = float(data.get("pad") or 0.25)
        name = safe_draft_name(data.get("name") or "")
        layout = data.get("layout") or "fill"
        sub = bool(data.get("sub"))
        title = (data.get("title") or "").strip()
        fade = float(data.get("fade") if data.get("fade") is not None else 0.12)
        transition = (data.get("transition") or "").strip()
        punch = (data.get("punch") or "").strip()
        cut_subs = bool(data.get("cut_subs", True))
        hint = (data.get("hint") or "marker").strip()
        bg_blur = data.get("bg_blur")
        if bg_blur is not None and str(bg_blur) != "":
            try:
                bg_blur = float(bg_blur)
            except (TypeError, ValueError):
                bg_blur = None

        if not os.path.exists(video):
            return self._json({"ok": False, "error": "找不到视频文件"})
        if not os.path.exists(srt):
            return self._json({"ok": False, "error": "找不到字幕文件"})
        if not picks:
            return self._json({"ok": False, "error": "还没有选句子"})

        buf = io.StringIO()
        ok = True
        info = {}
        try:
            with contextlib.redirect_stdout(buf):
                info = cmd_build(video, srt, picks, int(hook) if hook else None, pad, name,
                                 layout=layout, sub=sub, title=title,
                                 fade=fade, transition=transition, punch=punch,
                                 cut_subs=cut_subs, bg_blur=bg_blur,
                                 hint_mode=hint) or {}
        except SystemExit as e:
            ok = False
            buf.write(f"\n× 生成中断：{e}\n")
        except Exception as e:
            ok = False
            buf.write(f"\n× 出错了：{type(e).__name__}: {e}\n")

        prev = info.get("preview") or ""
        prev_url = ""
        if prev and os.path.exists(prev):
            prev_url = "/preview/" + urllib.parse.quote(os.path.basename(prev))
        sug = info.get("suggest") or ""
        sug_url = ""
        if sug and os.path.exists(sug):
            sug_url = "/preview/" + urllib.parse.quote(os.path.basename(sug))
        self._json({
            "ok": ok,
            "log": buf.getvalue(),
            "draft": os.path.join(md.DRAFT_ROOT, name),
            "name": name,
            "preview": prev_url,
            "suggest": sug_url,
        })


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>直播切片助手</title>
<style>
  :root{
    --bg:#0f1115; --panel:#171a21; --panel2:#1e222b; --line:#2a2f3a;
    --text:#e8eaed; --dim:#9aa3b2; --accent:#ff4d4f; --accent2:#ffb020;
    --ok:#22c55e;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
    font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px}
  .wrap{max-width:1080px;margin:0 auto;padding:28px 22px 60px}
  h1{margin:0 0 6px;font-size:22px;letter-spacing:.5px}
  .sub{margin:0 0 22px;color:var(--dim);font-size:13px}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .drop{position:relative;border:1.5px dashed var(--line);border-radius:12px;
    background:var(--panel);padding:26px 18px;text-align:center;cursor:pointer;
    transition:.15s}
  .drop:hover{border-color:#3d4553;background:var(--panel2)}
  .drop.on{border-color:var(--accent);background:#221a1b}
  .drop.done{border-style:solid;border-color:#2e4a38;background:#16211a}
  .drop .t{font-weight:600;margin-bottom:6px}
  .drop .d{color:var(--dim);font-size:12px;line-height:1.7}
  .drop .f{margin-top:10px;font-size:12px;color:var(--ok);word-break:break-all}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;
    margin-top:16px;overflow:hidden}
  .bar{display:flex;align-items:center;gap:12px;padding:12px 16px;
    border-bottom:1px solid var(--line);background:var(--panel2);flex-wrap:wrap}
  .bar .sp{flex:1}
  .rows{max-height:420px;overflow:auto}
  .row{display:flex;gap:10px;padding:9px 16px;border-bottom:1px solid #21252e;
    cursor:pointer;align-items:flex-start;line-height:1.5}
  .row:hover{background:#1b1f27}
  .row.sel{background:#241d1e;border-left:3px solid var(--accent);padding-left:13px}
  .row .no{color:var(--dim);font-variant-numeric:tabular-nums;min-width:34px;font-size:12px;padding-top:2px}
  .row .tx{flex:1;word-break:break-word}
  .row .tm{color:#6b7280;font-size:11px;font-variant-numeric:tabular-nums;padding-top:3px}
  .row .hk{font-size:11px;color:#111;background:var(--accent2);border-radius:4px;
    padding:1px 6px;font-weight:700;display:none}
  .row.hook .hk{display:inline-block}
  .row .set{font-size:11px;color:var(--dim);border:1px solid var(--line);
    border-radius:4px;padding:1px 6px;background:none;cursor:pointer}
  .row .set:hover{color:var(--text);border-color:#3d4553}
  .foot{padding:14px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  label{color:var(--dim);font-size:12px}
  input[type=text],input[type=number],select{background:#0c0e12;border:1px solid var(--line);
    color:var(--text);border-radius:8px;padding:7px 10px;font-size:13px;outline:none}
  select{cursor:pointer}
  input[type=text]{min-width:260px}
  input:focus{border-color:#4a5464}
  button.go{background:var(--accent);border:none;color:#fff;font-weight:700;
    padding:9px 22px;border-radius:9px;cursor:pointer;font-size:14px}
  button.go:disabled{background:#3a2a2b;color:#8b6b6c;cursor:not-allowed}
  button.ghost{background:none;border:1px solid var(--line);color:var(--dim);
    padding:6px 12px;border-radius:7px;cursor:pointer;font-size:12px}
  button.ghost:hover{color:var(--text)}
  pre.log{background:#0a0c10;border:1px solid var(--line);border-radius:10px;
    padding:14px 16px;margin-top:16px;font-size:12.5px;line-height:1.75;
    white-space:pre-wrap;color:#c9d1d9;max-height:340px;overflow:auto;display:none}
  .hint{color:var(--dim);font-size:12px;line-height:1.9;margin-top:14px}
  .hint b{color:var(--text)}
  .toast{position:fixed;left:50%;bottom:32px;transform:translateX(-50%) translateY(20px);
    background:#2a2f3a;color:#fff;padding:10px 18px;border-radius:9px;font-size:13px;
    opacity:0;transition:.2s;pointer-events:none;z-index:99}
  .toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
  .banner{display:none;background:#3a1d1f;border:1px solid #6b2b2e;color:#ffb4b4;
    padding:10px 14px;border-radius:10px;margin:0 0 16px;font-size:13px;line-height:1.6}
  .banner.show{display:block}
</style>
</head>
<body>
<div class="wrap">
  <h1>直播切片助手</h1>
  <p class="sub">挑句子 → 自动拼好 → 生成剪映草稿。只用你本机，不联网。</p>
  <div class="banner" id="banner">本地服务已断开 —— 那个黑色窗口被关掉了。请重新双击「start.bat」，再刷新这个页面。</div>

  <div class="grid">
    <div class="drop" id="dVideo">
      <div class="t">1 · 放进视频</div>
      <div class="d">点这里选文件，或直接把 mp4 拖到框里<br>（5 分钟录屏 ≈ 100–300MB，稍等几秒）</div>
      <div class="f" id="fVideo"></div>
    </div>
    <div class="drop" id="dSrt">
      <div class="t">2 · 放进字幕 SRT</div>
      <div class="d">剪映里「文本 → 智能字幕 → 识别」，再导出 SRT<br>识别完记得先改错别字</div>
      <div class="f" id="fSrt"></div>
    </div>
  </div>

  <div class="panel" id="panelList" style="display:none">
    <div class="bar">
      <b>3 · 挑句子</b>
      <span id="cnt" style="color:var(--dim)"></span>
      <span class="sp"></span>
      <button class="ghost" id="btnClear">清空选择</button>
    </div>
    <div class="rows" id="rows"></div>
    <div class="foot">
      <label>草稿名</label><input type="text" id="name">
      <label>前后留白</label><input type="number" id="pad" value="0.2" step="0.05" style="width:80px">
    </div>
    <div class="foot" style="padding-top:0">
      <label>版式</label>
      <select id="layout" title="发抖音选第一个：9:16 竖屏，视频居中偏上，上下留位放标题和字幕">
        <option value="blur">竖屏 9:16 · 模糊背景（推荐）</option>
        <option value="split">竖屏分栏 · 黑底新字幕在下</option>
        <option value="fill">跟素材走 · 横屏满屏</option>
      </select>
      <label title="只作用于最底下那层「模糊背景」，中间的主视频一直是清晰的。嫌背景太糊就调轻，嫌背景抢眼就调重">背景模糊</label>
      <select id="bgBlur" title="跟剪映里「模糊」特效的那根滑杆是同一个参数">
        <option value="0">不糊 · 只放大</option>
        <option value="20">很轻</option>
        <option value="35" selected>轻 · 默认</option>
        <option value="55">中 · 隐约有形</option>
        <option value="80">强 · 只剩色块</option>
        <option value="100">极强 · 一团色雾</option>
      </select>
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
        <input type="checkbox" id="subOn" style="width:auto"> 叠加我自己的字幕
      </label>
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer"
             title="把画面里烧死的那行旧字幕原位补掉（delogo 插值），画面比例完全不变；第一次会花一两分钟处理，之后复用缓存">
        <input type="checkbox" id="cutSubs" checked style="width:auto"> 抹掉画面里的原字幕
      </label>
      <label>标题</label><input type="text" id="title" placeholder="竖屏版式才用" style="min-width:150px">
      <label>转场</label>
      <select id="transition">
        <option value="">不加（硬切，最快）</option>
        <option value="叠化">叠化 0.3s</option>
        <option value="闪白">闪白</option>
        <option value="闪黑">闪黑</option>
        <option value="模糊">模糊</option>
        <option value="故障">故障</option>
      </select>
      <label>接缝淡化</label><input type="number" id="fade" value="0.12" step="0.02" min="0" max="0.3" style="width:70px" title="每段首尾音量渐入渐出秒数，0 = 关闭">
      <label title="填最炸的那几句的编号：会自动在前面留 0.2 秒静音 + 画面推到 1.12 倍">爆点句号</label><input type="text" id="punch" value="" placeholder="如 42,88,136" style="width:120px">
      <label title="标记轨＝把提示做成时间轴上的静音占位音频，文件名就是提示内容，画面里看不见；画面标签＝会盖住一点画面">提示</label>
      <select id="hint">
        <option value="marker" selected>标记轨 · 只看时间轴（推荐）</option>
        <option value="all">标记轨 + 画面钩子标签</option>
        <option value="one">只画面钩子标签（旧）</option>
      </select>
      <span class="sp"></span>
      <button class="go" id="btnBuild">生成剪映草稿</button>
    </div>
  </div>

  <div id="shotWrap" style="display:none;margin-top:16px">
    <div style="color:var(--dim);font-size:12px;margin-bottom:8px">
      版面预览（按草稿真实位置合成，用来核对字幕挡不挡脸；双击可看大图）
    </div>
    <img id="shot" style="max-width:420px;width:100%;border-radius:10px;border:1px solid var(--line)">
  </div>

  <div id="sugWrap" style="display:none;margin-top:14px">
    <a id="sugLink" href="#" target="_blank" style="display:inline-block;padding:10px 16px;border-radius:9px;
       background:#fdf3ee;border:1px solid #e8bda9;color:#8f3c1a;text-decoration:none;font-size:13px">
      打开「配乐 / 音效 / 花字 / 标题」建议清单 →</a>
    <span style="color:var(--dim);font-size:12px;margin-left:10px">剪的时候开着对照，别凭记忆</span>
  </div>

  <pre class="log" id="log"></pre>

  <div class="hint">
    <b>点一下</b>选中／取消，<b>按住 Shift 点</b>可以选一整段连续区间。<br>
    <b>「钩」按钮</b>把那一句提到视频最前面当开头钩子（前置高潮，抖音切片的规矩）。<br>
    <b>选段要连续</b>，跳着选会把两件不相干的事拼在一起，成片会跳。<br>
    <a href="/selftest" target="_blank" style="color:#8b93a1">上传一直失败？点这里做一次自检（会显示服务状态）</a>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
const state = { video:null, srt:null, entries:[], sel:new Set(), hook:null };

// 「背景模糊」只在模糊背景版式下有效果，切到别的版式就灰掉，免得以为调了却没变
(function(){
  const lay = document.getElementById('layout'), bg = document.getElementById('bgBlur');
  function upd(){
    const on = lay.value === 'blur' || lay.value === 'auto';
    bg.disabled = !on;
    bg.style.opacity = on ? '1' : '.4';
  }
  lay.addEventListener('change', upd);
  upd();
})();

function toast(msg){
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(t._h); t._h = setTimeout(()=>t.classList.remove('show'), 2200);
}

function bindDrop(el, kind, accept, onDone){
  const input = document.createElement('input');
  input.type='file'; input.accept=accept; input.style.display='none';
  document.body.appendChild(input);
  el.addEventListener('click', ()=>input.click());
  input.addEventListener('change', ()=>{ if(input.files[0]) handle(input.files[0]); });
  ['dragenter','dragover'].forEach(e=>el.addEventListener(e, ev=>{
    ev.preventDefault(); el.classList.add('on');
  }));
  ['dragleave','drop'].forEach(e=>el.addEventListener(e, ev=>{
    ev.preventDefault(); el.classList.remove('on');
  }));
  el.addEventListener('drop', ev=>{
    const f = ev.dataTransfer.files[0]; if(f) handle(f);
  });

  async function postFile(url, file){
    let lastErr;
    for(let i=0;i<2;i++){
      try { return await fetch(url, {method:'POST', body:file}); }
      catch(e){ lastErr = e; await new Promise(r=>setTimeout(r,800)); }
    }
    throw lastErr;
  }

  async function handle(file){
    el.querySelector('.d').textContent = '正在读取…';
    try{
      const url = '/api/upload?kind='+kind+'&name='+encodeURIComponent(file.name);
      const r = await postFile(url, file);
      const j = await r.json();
      if(!j.ok) throw new Error(j.error || ('服务返回 '+r.status));
      el.classList.add('done');
      el.querySelector('.d').textContent = '已就绪（'+(j.size/1048576).toFixed(1)+'MB）';
      el.querySelector('.f').textContent = file.name;
      onDone(j.path);
    }catch(e){
      const msg = (e instanceof TypeError)
        ? '连不上本地服务（黑窗口是不是被关了？重新双击「start.bat」）'
        : e.message;
      el.querySelector('.d').textContent = '读取失败：' + msg;
      toast('读取失败：' + msg);
      console.error('[upload]', e);
    }
  }
}

bindDrop(document.getElementById('dVideo'), 'video', 'video/*', p=>{ state.video = p; });
bindDrop(document.getElementById('dSrt'), 'srt', '.srt,text/plain', p=>{
  state.srt = p; loadList();
});

// 守护：每 4 秒探一次服务，黑窗口被关了就红条提醒，别让人对着死页面瞎试
async function ping(){
  const b = document.getElementById('banner');
  try{
    const r = await fetch('/api/health', {cache:'no-store'});
    b.classList.toggle('show', !r.ok);
  }catch(e){
    b.classList.add('show');
  }
}
ping(); setInterval(ping, 4000);

async function loadList(){
  if(!state.srt) return;
  const r = await fetch('/api/list', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({srt: state.srt})});
  const j = await r.json();
  if(!j.ok){ toast(j.error || '读字幕失败'); return; }
  state.entries = j.entries;
  state.sel = new Set();
  state.hook = null;
  render();
  document.getElementById('panelList').style.display = 'block';
  const base = (state.video||'录屏').split(/[\\/]/).pop().replace(/\.[^.]+$/, '');
  document.getElementById('name').value = '切片-' + base;
  if(!j.entries.length) toast('SRT 里没解析出字幕，检查导出格式');
}

let lastIdx = null;
function onRowClick(i, ev){
  if(ev.shiftKey && lastIdx !== null){
    const a = Math.min(lastIdx, i), b = Math.max(lastIdx, i);
    for(let k=a;k<=b;k++) state.sel.add(k);
  }else{
    if(state.sel.has(i)) state.sel.delete(i); else state.sel.add(i);
  }
  lastIdx = i;
  render();
}

function render(){
  const rows = document.getElementById('rows');
  rows.innerHTML = '';
  state.entries.forEach(e=>{
    const d = document.createElement('div');
    d.className = 'row' + (state.sel.has(e.i)?' sel':'') + (state.hook===e.i?' hook':'');
    d.innerHTML =
      '<div class="no">'+e.i+'</div>'+
      '<div class="tx"></div>'+
      '<div class="tm">'+e.start.toFixed(1)+'s</div>'+
      '<div class="hk">钩子</div>'+
      '<button class="set">'+(state.hook===e.i?'取消钩':'设钩')+'</button>';
    d.querySelector('.tx').textContent = e.text;
    d.addEventListener('click', ev=>{
      if(ev.target.classList.contains('set')) return;
      onRowClick(e.i, ev);
    });
    d.querySelector('.set').addEventListener('click', ev=>{
      ev.stopPropagation();
      state.hook = (state.hook===e.i) ? null : e.i;
      if(state.hook) state.sel.add(e.i);
      render();
    });
    rows.appendChild(d);
  });
  const n = state.sel.size;
  const picks = [...state.sel].sort((a,b)=>a-b);
  let est = '——';
  if(n){
    const a = state.entries[picks[0]-1], b = state.entries[picks[n-1]-1];
    est = (b.end - a.start).toFixed(0) + ' 秒左右';
  }
  document.getElementById('cnt').textContent =
    '已选 '+n+' 句'+(n?('　预计成片 '+est):'');
  document.getElementById('btnBuild').disabled = (n===0 || !state.video);
}

document.getElementById('btnClear').addEventListener('click', ()=>{
  state.sel.clear(); state.hook = null; render();
});

document.getElementById('btnBuild').addEventListener('click', async ()=>{
  if(!state.video){ toast('还没放视频'); return; }
  const picks = [...state.sel].sort((a,b)=>a-b);
  const btn = document.getElementById('btnBuild');
  btn.disabled = true; btn.textContent = '生成中…';
  const log = document.getElementById('log');
  log.style.display='block';
  log.textContent = document.getElementById('cutSubs').checked
    ? '正在生成草稿…\n（勾了「抹掉画面里的原字幕」时，第一条视频要先处理 1–2 分钟，之后同样的片子会直接用缓存，几秒就好）'
    : '正在生成草稿…';
  try{
    const r = await fetch('/api/build', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        video: state.video, srt: state.srt, picks: picks,
        hook: state.hook, pad: parseFloat(document.getElementById('pad').value)||0.2,
        name: document.getElementById('name').value,
        layout: document.getElementById('layout').value,
        bg_blur: parseFloat(document.getElementById('bgBlur').value),
        sub: document.getElementById('subOn').checked,
        title: document.getElementById('title').value,
        transition: document.getElementById('transition').value,
        fade: parseFloat(document.getElementById('fade').value),
        punch: document.getElementById('punch').value,
        hint: document.getElementById('hint').value,
        cut_subs: document.getElementById('cutSubs').checked
      })});
    const j = await r.json();
    log.textContent = j.log || j.error || '';
    if(j.ok){
      log.textContent += '\n✅ 完成。草稿文件夹：\n' + j.draft +
        '\n\n剪映里打开它，然后：照着「施工标记」轨上的提示加音效（文件名就是提示，画面里看不见）' +
        ' → 加完删掉这条轨 → 配花字和 BGM → 导出';
      if(j.preview){
        const w = document.getElementById('shotWrap'), img = document.getElementById('shot');
        img.src = j.preview + '?t=' + Date.now();
        img.onclick = ()=>window.open(img.src, '_blank');
        w.style.display = 'block';
      }
      const sw = document.getElementById('sugWrap');
      if(j.suggest){
        document.getElementById('sugLink').href = j.suggest;
        sw.style.display = 'block';
      }else{
        sw.style.display = 'none';
      }
      toast('草稿已生成');
    }else{
      log.textContent += '\n❌ 没生成成功。半成品目录已自动清理，剪映里不会多出打不开的草稿。' +
        '\n把上面这段日志发我，我按报错定位。';
      toast('生成失败，看下面日志');
    }
  }catch(e){
    log.textContent = '请求失败：'+e.message;
  }finally{
    btn.disabled = false; btn.textContent = '生成剪映草稿';
  }
});
</script>
</body>
</html>
"""


SELFTEST_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>selftest</title></head>
<body>
<div id="out">RUNNING</div>
<script>
(async () => {
  const out = document.getElementById('out');
  const lines = [];
  try {
    const r0 = await fetch('/api/health');
    lines.push('HEALTH ' + r0.status + ' ' + (await r0.text()));
  } catch (e) { lines.push('HEALTH_ERR ' + e.message); }
  try {
    const blob = new Blob([new Uint8Array(1024 * 1024)], {type: 'video/mp4'});
    const r = await fetch('/api/upload?kind=video&name=selftest_1mb.mp4',
                          {method: 'POST', body: blob});
    lines.push('UPLOAD ' + r.status + ' ' + (await r.text()));
  } catch (e) { lines.push('UPLOAD_ERR ' + e.message); }
  out.textContent = lines.join(' ;; ');
  document.title = 'DONE';
})();
</script>
</body></html>
"""


def pick_port():
    for p in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise SystemExit("端口 8765–8769 都被占用了，关掉其他程序再试。")


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW("切片助手 —— 这个窗口别关")
        except Exception:
            pass

    port = pick_port()
    url = f"http://127.0.0.1:{port}/"
    print("=" * 60)
    print("  直播切片助手已启动")
    print("=" * 60)
    print(f"  浏览器地址：{url}")
    print()
    print("  ★ 这个黑色窗口不要关 ★")
    print("    它就是服务本身。关了它，网页就传不了文件、也生成不了草稿。")
    print("    不要在这个窗口里按 Ctrl+C。用完了最后再关。")
    print()
    print("  如果浏览器没自动打开，手动把上面的地址粘到地址栏。")
    print("=" * 60)

    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
