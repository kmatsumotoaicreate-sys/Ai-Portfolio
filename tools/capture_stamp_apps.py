# -*- coding: utf-8 -*-
"""stamp-app の2つのアプリを Edge（画面なし）で操作し、制作ノート用の動く画面（WebP）を作る。
追加のソフトは使わない（Python標準ライブラリ＋PCに入っている Edge のみ）。"""
import base64, functools, http.server, json, os, socket, struct, subprocess, sys, threading, time, urllib.request

APP = r"D:\⑪　松本和晃\Claudcode\資料製作\stamp-app"
OUT = r"D:\⑪　松本和晃\Claudcode\資料製作\制作ノート\img"
TMP = os.path.dirname(os.path.abspath(__file__))
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
HTTP_PORT, CDP_PORT = 8765, 9377
W, H = 390, 760


# ---------- 最小限の WebSocket クライアント ----------
class WS:
    def __init__(self, url):
        rest = url[len("ws://"):]
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.s = socket.create_connection((host, int(port)))
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(1)
        assert b" 101 " in buf.split(b"\r\n")[0], buf
        self.n = 0

    def _read(self, n):
        b = bytearray()
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c:
                raise EOFError
            b += c
        return bytes(b)

    def _send(self, op, data):
        hdr = bytes([0x80 | op])
        ln = len(data)
        if ln < 126:
            hdr += bytes([0x80 | ln])
        elif ln < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", ln)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", ln)
        mask = os.urandom(4)
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _recv(self):
        msg = b""
        while True:
            b0, b1 = self._read(2)
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read(8))[0]
            data = self._read(ln)
            op = b0 & 0x0F
            if op == 9:
                self._send(10, data)
                continue
            if op == 8:
                raise EOFError
            msg += data
            if b0 & 0x80:
                return msg

    def call(self, method, **params):
        self.n += 1
        self._send(1, json.dumps({"id": self.n, "method": method, "params": params}).encode())
        while True:
            m = json.loads(self._recv())
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m["result"]


# ---------- 動く WebP の組み立て ----------
def chunk(tag, data):
    return tag + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")


def frame_chunks(webp):
    """1枚の WebP から画像本体のチャンク（ALPH / VP8 / VP8L）だけを取り出す"""
    assert webp[:4] == b"RIFF" and webp[8:12] == b"WEBP"
    out, pos, alpha = b"", 12, False
    while pos + 8 <= len(webp):
        tag = webp[pos:pos + 4]
        ln = struct.unpack("<I", webp[pos + 4:pos + 8])[0]
        data = webp[pos + 8:pos + 8 + ln]
        if tag in (b"ALPH", b"VP8 ", b"VP8L"):
            out += chunk(tag, data)
            alpha = alpha or tag == b"ALPH"
        pos += 8 + ln + (ln & 1)
    return out, alpha


def u24(n):
    return struct.pack("<I", n)[:3]


def build_anim(frames, w, h):
    body, alpha = b"", False
    for webp, ms in frames:
        fc, a = frame_chunks(webp)
        alpha = alpha or a
        body += chunk(b"ANMF", u24(0) + u24(0) + u24(w - 1) + u24(h - 1) + u24(ms) + b"\x02" + fc)
    vp8x = chunk(b"VP8X", bytes([0x02 | (0x10 if alpha else 0)]) + b"\0\0\0" + u24(w - 1) + u24(h - 1))
    anim = chunk(b"ANIM", b"\xff\xff\xff\xff" + struct.pack("<H", 0))
    payload = b"WEBP" + vp8x + anim + body
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


# ---------- ブラウザ操作 ----------
RING = """(()=>{const el=%s; if(!el) return 'NOTFOUND';
 const r0=el.getBoundingClientRect(); if(r0.top<70||r0.bottom>innerHeight-20) el.scrollIntoView({block:'center'});
 const r=el.getBoundingClientRect(); let d=document.getElementById('__tap'); if(!d){d=document.createElement('div');d.id='__tap';document.body.appendChild(d);}
 d.style.cssText='position:fixed;z-index:999999;pointer-events:none;width:46px;height:46px;border-radius:50%%;border:3px solid #fff;background:rgba(255,255,255,.38);box-shadow:0 0 0 2px rgba(0,0,0,.45);left:'+(r.left+r.width/2-23)+'px;top:'+(r.top+r.height/2-23)+'px';
 return 'ok';})()"""
CLICK = "(()=>{const el=%s; const d=document.getElementById('__tap'); if(d) d.remove(); el.click(); return 'ok';})()"

SAMPLE_PHOTO = """(async()=>{
 const c=document.createElement('canvas'); c.width=1200; c.height=900; const x=c.getContext('2d');
 let g=x.createLinearGradient(0,0,0,560); g.addColorStop(0,'#5fa8e8'); g.addColorStop(1,'#cfe8fb'); x.fillStyle=g; x.fillRect(0,0,1200,900);
 x.fillStyle='#fff3b0'; x.beginPath(); x.arc(960,170,70,0,7); x.fill();
 x.fillStyle='rgba(255,255,255,.9)'; [[220,150,60],[290,135,75],[370,155,55],[640,230,45],[700,220,60],[770,236,42]].forEach(([a,b,r])=>{x.beginPath();x.arc(a,b,r,0,7);x.fill();});
 x.fillStyle='#7d8ea3'; x.beginPath(); x.moveTo(60,600); x.lineTo(430,210); x.lineTo(800,600); x.fill();
 x.fillStyle='#f4f8fc'; x.beginPath(); x.moveTo(430,210); x.lineTo(345,300); x.lineTo(400,285); x.lineTo(440,320); x.lineTo(480,280); x.lineTo(520,305); x.fill();
 x.fillStyle='#5f7088'; x.beginPath(); x.moveTo(520,600); x.lineTo(860,300); x.lineTo(1200,560); x.lineTo(1200,600); x.fill();
 x.fillStyle='#eef3f8'; x.beginPath(); x.moveTo(860,300); x.lineTo(800,353); x.lineTo(845,345); x.lineTo(880,372); x.lineTo(930,352); x.fill();
 x.fillStyle='#4f9a4a'; x.beginPath(); x.moveTo(0,640); x.quadraticCurveTo(300,520,620,610); x.quadraticCurveTo(900,680,1200,570); x.lineTo(1200,900); x.lineTo(0,900); x.fill();
 x.fillStyle='#63b35a'; x.beginPath(); x.moveTo(0,740); x.quadraticCurveTo(400,640,800,730); x.quadraticCurveTo(1000,770,1200,720); x.lineTo(1200,900); x.lineTo(0,900); x.fill();
 x.fillStyle='#d9c79a'; x.beginPath(); x.moveTo(520,900); x.quadraticCurveTo(600,760,560,640); x.lineTo(600,640); x.quadraticCurveTo(680,760,700,900); x.fill();
 const blob=await new Promise(r=>c.toBlob(r,'image/jpeg',.9));
 const dt=new DataTransfer(); dt.items.add(new File([blob],'sample.jpg',{type:'image/jpeg'}));
 const inp=document.getElementById('galleryInput'); inp.files=dt.files; inp.dispatchEvent(new Event('change',{bubbles:true}));
 return 'ok';})()"""


class Shooter:
    def __init__(self, ws, name):
        self.ws, self.name, self.frames = ws, name, []

    def js(self, expr):
        r = self.ws.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:600])
        return r["result"].get("value")

    def shot(self, ms):
        data = base64.b64decode(self.ws.call("Page.captureScreenshot", format="webp", quality=82)["data"])
        self.frames.append((data, ms))
        with open(os.path.join(TMP, f"{self.name}_{len(self.frames):02d}.webp"), "wb") as f:
            f.write(data)

    def tap(self, sel, after=0.6, hold=1200, ring_ms=450):
        v = self.js(RING % sel)
        if v != "ok":
            raise RuntimeError(f"要素が見つかりません: {sel}")
        time.sleep(0.15)
        self.shot(ring_ms)
        self.js(CLICK % sel)
        time.sleep(after)
        if hold:
            self.shot(hold)

    def open(self, page):
        self.ws.call("Page.navigate", url=f"http://127.0.0.1:{HTTP_PORT}/{page}")
        time.sleep(2.5)
        self.js("document.fonts.ready.then(()=>'ok')")

    def save(self, anim_name, still_name, still_index=-1):
        with open(os.path.join(OUT, anim_name), "wb") as f:
            f.write(build_anim(self.frames, W, H))
        with open(os.path.join(OUT, still_name), "wb") as f:
            f.write(self.frames[still_index][0])
        print(self.name, len(self.frames), "frames", os.path.getsize(os.path.join(OUT, anim_name)) // 1024, "KB")


def scenario_pro(ws):
    s = Shooter(ws, "pro")
    s.open("index.html")
    s.shot(1100)
    s.js(RING % "document.getElementById('pickBtn')")
    time.sleep(0.15)
    s.shot(450)
    s.js("document.getElementById('__tap').remove()")
    s.js(SAMPLE_PHOTO)
    time.sleep(1.5)
    s.shot(1300)
    s.tap("document.getElementById('gachaBtn')", after=1.2, hold=0)
    s.js("scrollTo(0,0)")
    time.sleep(0.3)
    s.shot(1700)
    s.tap("document.querySelector('#modebar [data-mode=manga]')", after=0.6, hold=900)
    s.tap("document.querySelector('#mgLines button')", after=0.8, hold=900)
    s.js("scrollTo(0,0)")
    time.sleep(0.4)
    s.shot(2600)
    s.save("stamp_anim.webp", "stamp.webp")


def scenario_maker(ws):
    s = Shooter(ws, "maker")
    s.open("maker.html")
    s.shot(1300)
    s.tap("document.querySelector('#homeTabs [data-v=make]')", after=0.6, hold=1300)
    s.tap("[...document.querySelectorAll('.method')].find(b=>b.textContent.includes('ハンコ'))", after=1.2, hold=0)
    s.js("scrollTo(0,0)")
    time.sleep(0.3)
    s.shot(1300)
    s.tap("document.querySelector('#hkType [data-v=date]')", after=1.0, hold=1600)
    still = len(s.frames) - 1
    s.tap("document.querySelector('#hkTabs [data-p=hkPColor]')", after=0.5, hold=800)
    s.tap("[...document.querySelectorAll('#hkColor button')].find(b=>b.textContent.includes('青'))", after=0.9, hold=1500)
    s.tap("document.getElementById('hkNext')", after=1.3, hold=0)
    s.js("scrollTo(0,0)")
    time.sleep(0.3)
    s.shot(2600)
    s.save("maker_anim.webp", "maker.webp", still)


def scenario_score_probe(ws):
    s = Shooter(ws, "probe")
    s.open("score/score.html")
    s.shot(1000)
    s.js("(()=>{const b=document.getElementById('gSkip'); if(b) b.click(); return 'ok'})()")
    time.sleep(0.8)
    s.shot(1000)
    print(s.js("JSON.stringify({cards:document.getElementById('cards').innerText.slice(0,300), rule:document.getElementById('ruleTxt').innerText, open:[...document.querySelectorAll('dialog[open]')].map(d=>d.id)})"))


def scenario_score(ws):
    """とくてんボード：4人の点数を入れて順位が並び替わるところ"""
    s = Shooter(ws, "score")
    s.open("score/score.html")
    s.js("(()=>{const b=document.getElementById('gSkip'); if(b) b.click(); return 'ok'})()")
    time.sleep(0.8)
    s.shot(1300)
    entries = [("1", ["3", "0"]), ("2", ["4", "5"]), ("3", ["2", "0"]), ("4", ["1", "5"])]
    for i, (n, keys) in enumerate(entries):
        if i == 0:
            s.tap("document.querySelector('#cards > :nth-child(1)')", after=0.7, hold=700)
        for j, k in enumerate(keys):
            s.tap(f"document.querySelector('#dlgInput [data-k=\"{k}\"]')", after=0.25, hold=(800 if j == len(keys) - 1 else 0))
        last = i == len(entries) - 1
        s.tap("document.getElementById('" + ("inOk" if last else "inOkNext") + "')", after=1.0, hold=(0 if last else 700))
    time.sleep(0.6)
    s.js("scrollTo(0,0)")
    s.shot(3000)
    s.save("score_anim.webp", "score.webp")


def main():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=APP)
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={CDP_PORT}",
                             "--user-data-dir=" + os.path.join(TMP, "edgeprof"), "--no-first-run",
                             "--hide-scrollbars", "--lang=ja", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list"))
                page = [t for t in targets if t["type"] == "page"]
                if page:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        ws = WS(page[0]["webSocketDebuggerUrl"])
        ws.call("Page.enable")
        ws.call("Runtime.enable")
        ws.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=True)
        which = sys.argv[1:] or ["pro", "maker"]
        if "pro" in which:
            scenario_pro(ws)
        if "maker" in which:
            scenario_maker(ws)
        if "scoreprobe" in which:
            scenario_score_probe(ws)
        if "score" in which:
            scenario_score(ws)
    finally:
        edge.terminate()
        srv.shutdown()


main()
