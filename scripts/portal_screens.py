"""TC-09 (R-10), емуляція: знімки сторінки входу captive portal у режимі пристрою Chrome DevTools
(ширина екрана, щільність пікселів, мобільний user-agent, сенсорний ввід) — як DevTools → Device Mode.
Реальні iPhone / Android / Mac цим не замінюються, це перевірка верстки до тесту на реальних пристроях.

Сторінка порталу гостьової зони відкривається через SOCKS-проксі (ssh -D) на VM alpine-client.
Лише стандартна бібліотека Python + встановлений Google Chrome.

Запуск:  python scripts/portal_screens.py [папка_для_знімків]
"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
KEY = os.path.join(BASE, "..", "secrets", "id_67space")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORTAL = "http://10.67.30.1:8002/index.html"
SOCKS, CDP = 18081, 9333
DEVICES = [  # назва, ширина, висота (CSS px), щільність, мобільний, user-agent
    ("iphone", 390, 844, 3, True,
     "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"),
    ("android", 412, 915, 2.625, True,
     "Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"),
    ("windows", 1366, 768, 1, False,
     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    ("macos", 1440, 900, 2, False,
     "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"),
]


class WS:
    """Мінімальний клієнт WebSocket (RFC 6455) для DevTools-протоколу."""

    def __init__(self, url):
        host, rest = url[len("ws://"):].split("/", 1)
        h, p = host.split(":")
        self.s = socket.create_connection((h, int(p)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /{rest} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.s.recv(1)
        assert b" 101 " in resp.split(b"\r\n")[0], resp
        self.n = 0

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("websocket closed")
            buf += chunk
        return buf

    def _recv(self):
        data = b""
        while True:
            b1, b2 = self._recv_exact(2)
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._recv_exact(8))[0]
            data += self._recv_exact(ln)
            if b1 & 0x80:
                return json.loads(data)

    def call(self, method, **params):
        self.n += 1
        payload = json.dumps({"id": self.n, "method": method, "params": params}).encode()
        mask = os.urandom(4)
        head = bytes([0x81])
        ln = len(payload)
        head += bytes([0x80 | ln]) if ln < 126 else (bytes([0x80 | 126]) + struct.pack(">H", ln) if ln < 65536
                                                     else bytes([0x80 | 127]) + struct.pack(">Q", ln))
        self.s.sendall(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))
        while True:
            msg = self._recv()
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})


def shoot(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    tunnel = subprocess.Popen(["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-N",
                               "-D", str(SOCKS), "root@192.168.56.20"])
    profile = tempfile.mkdtemp(prefix="67space-chrome-")
    chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}",
                               f"--remote-debugging-port={CDP}", f"--proxy-server=socks5://127.0.0.1:{SOCKS}", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    saved = []
    try:
        ws_url = None
        for _ in range(60):
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json"))
                ws_url = next(p["webSocketDebuggerUrl"] for p in pages if p["type"] == "page")
                break
            except Exception:  # noqa: BLE001 — Chrome ще стартує
                time.sleep(0.5)
        if not ws_url:
            raise RuntimeError("Chrome не відкрив порт DevTools")
        ws = WS(ws_url)
        ws.call("Page.enable")
        for name, w, h, dpr, mobile, ua in DEVICES:
            ws.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=dpr, mobile=mobile)
            ws.call("Emulation.setTouchEmulationEnabled", enabled=mobile)
            ws.call("Emulation.setUserAgentOverride", userAgent=ua)
            ws.call("Page.navigate", url=PORTAL)
            for _ in range(30):   # сторінка спершу питає статус у порталу й лише тоді показує форму
                time.sleep(0.5)
                m = ws.call("Runtime.evaluate", returnByValue=True, expression=(
                    "JSON.stringify({vw: innerWidth, sw: document.documentElement.scrollWidth,"
                    " form: !!document.getElementById('login_password') &&"
                    " !document.getElementById('login_password').classList.contains('hidden')})"))
                info = json.loads(m["result"]["value"])
                if info["form"]:
                    break
            time.sleep(0.5)
            png = base64.b64decode(ws.call("Page.captureScreenshot", format="png")["data"])
            path = os.path.join(out_dir, f"tc09-{name}.png")
            open(path, "wb").write(png)
            fits = info["sw"] <= info["vw"]
            saved.append({"device": name, "viewport": f"{w}x{h}", "dpr": dpr, "form_shown": info["form"],
                          "page_width": info["sw"], "fits_screen": fits, "file": os.path.basename(path)})
            print(f"  {name:8} {w}x{h} @{dpr}: форма {'є' if info['form'] else 'НЕМАЄ'}, ширина сторінки "
                  f"{info['sw']} / екран {info['vw']} -> {'вміщується' if fits else 'ГОРИЗОНТАЛЬНА ПРОКРУТКА'}")
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(chrome.pid)], capture_output=True)  # разом із дочірніми
        tunnel.terminate()
    with open(os.path.join(out_dir, "tc09-emulation.json"), "w", encoding="utf-8") as f:
        json.dump(saved, f, ensure_ascii=False, indent=1)
    return saved


if __name__ == "__main__":
    shoot(sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "..", "evidence", "sprint2-opnsense", "screens"))
