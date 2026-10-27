"""Sprint 2: інструмент моніторингу 67space для адміністратора (R-05) і журнал підключень (R-14).

Кожні POLL_S секунд опитує OPNsense через API:
  * активні сесії captive portal за сегментами (хто, IP, MAC, з якого часу, трафік);
  * навантаження каналу: швидкість на інтерфейсах STUDENTS / TEACHERS / GUESTS / WAN;
  * стан точок доступу AP-1..AP-3 (перевірка ping від Monit на OPNsense).
Панель: http://127.0.0.1:8067 (оновлюється сама), дані: /status.json, журнали: /journal.csv, /events.csv.

Журнал підключень data/connections.csv: роль, користувач, пристрій (MAC), IP, початок і кінець сесії,
трафік. Записи старші за RETENTION_DAYS видаляються (строк — припущення моделі, OQ-6).

Запуск:  python scripts/monitor.py          (Ctrl+C — зупинити)
"""
import csv
import html
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import opn

POLL_S = 10
PORT = 8067
RETENTION_DAYS = 31
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
JOURNAL = os.path.join(DATA, "connections.csv")
EVENTS = os.path.join(DATA, "events.csv")
SEGMENTS = {  # зона порталу -> (сегмент, інтерфейс OPNsense)
    0: ("STUDENTS", "opt1"),
    1: ("TEACHERS", "opt2"),
    2: ("GUESTS", "opt3"),
}
ROLE = {"STUDENTS": "студент", "TEACHERS": "викладач", "GUESTS": "гість"}
J_FIELDS = ["session_id", "role", "segment", "user", "mac", "ip", "start", "end", "bytes_in", "bytes_out"]
E_FIELDS = ["time", "object", "event", "detail"]

os.makedirs(DATA, exist_ok=True)


def ts(epoch):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(epoch)))


def append(path, fields, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


def write_journal(rows):
    """Атомарний запис: спершу тимчасовий файл, потім заміна — обірваний запис не псує журнал."""
    tmp = JOURNAL + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=J_FIELDS)
        w.writeheader()
        w.writerows(rows)
    for _ in range(5):
        try:
            os.replace(tmp, JOURNAL)
            return
        except PermissionError:   # Windows: файл саме читає інший процес
            time.sleep(0.2)
    os.replace(tmp, JOURNAL)


def read(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def purge_old():
    """Строк зберігання журналу: прибрати записи, що закінчились понад RETENTION_DAYS днів тому."""
    limit = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - RETENTION_DAYS * 86400))
    rows = read(JOURNAL)
    keep = [r for r in rows if not r["end"] or r["end"] >= limit]
    if len(keep) != len(rows):
        write_journal(keep)


class Monitor:
    def __init__(self, journal=True):
        self.journal = journal  # False — лише показники (журнал веде одна запущена панель)
        self.state = {"updated": None, "segments": {}, "links": {}, "aps": {}, "gateway": {}, "errors": []}
        self.open = {}          # session_id -> рядок журналу (сесії, що тривають)
        self.prev_bytes = None  # (час, {інтерфейс: (in, out)}) для розрахунку швидкості
        self.ap_prev = {}
        self.lock = threading.Lock()
        for r in (read(JOURNAL) if journal else []):  # після перезапуску продовжити незакриті сесії
            if not r["end"]:
                self.open[r["session_id"]] = r

    # ---------------------------------------------------------- збір даних
    def sessions(self):
        seg = {}
        now_ids = set()
        for zone, (name, _) in SEGMENTS.items():
            rows = opn.api(f"/api/captiveportal/session/list/{zone}") or []
            rows = rows if isinstance(rows, list) else []
            seg[name] = [{"user": s["userName"], "ip": s["ipAddress"], "mac": s.get("macAddress", ""),
                          "since": ts(s["startTime"]), "bytes_in": s.get("bytes_in", 0),
                          "bytes_out": s.get("bytes_out", 0)} for s in rows]
            for s in rows:
                sid = s["sessionId"]
                now_ids.add(sid)
                if sid not in self.open:
                    self.open[sid] = {"session_id": sid, "role": ROLE[name], "segment": name, "user": s["userName"],
                                      "mac": s.get("macAddress", ""), "ip": s["ipAddress"], "start": ts(s["startTime"]),
                                      "end": "", "bytes_in": s.get("bytes_in", 0), "bytes_out": s.get("bytes_out", 0)}
                    self.rewrite_journal()
                else:
                    self.open[sid].update(bytes_in=s.get("bytes_in", 0), bytes_out=s.get("bytes_out", 0))
        for sid in [k for k in self.open if k not in now_ids]:  # сесія зникла -> записати кінець
            self.close_session(self.open.pop(sid), ts(time.time()))
        return seg

    def rewrite_journal(self):
        if not self.journal:
            return
        rows = {r["session_id"]: r for r in read(JOURNAL)}
        rows.update({k: dict(v) for k, v in self.open.items()})
        write_journal(rows.values())

    def close_session(self, last, end):
        if not self.journal:
            return
        rows = read(JOURNAL)
        for r in rows:
            if r["session_id"] == last["session_id"] and not r["end"]:
                r.update(end=end, bytes_in=last["bytes_in"], bytes_out=last["bytes_out"])
        write_journal(rows)
        purge_old()

    def links(self):
        ifs = opn.api("/api/diagnostics/traffic/interface").get("interfaces", {})
        now = time.time()
        cur = {k: (int(v.get("bytes received", 0)), int(v.get("bytes transmitted", 0)))
               for k, v in ifs.items() if k in ("opt1", "opt2", "opt3", "wan")}
        out = {}
        if self.prev_bytes:
            t0, prev = self.prev_bytes
            dt = max(now - t0, 1)
            for k, (bi, bo) in cur.items():
                pi, po = prev.get(k, (bi, bo))
                # з боку OPNsense: received = від клієнтів (вивантаження), transmitted = до клієнтів
                out[k] = {"up_mbit": round(max(bi - pi, 0) * 8 / dt / 1e6, 2),
                          "down_mbit": round(max(bo - po, 0) * 8 / dt / 1e6, 2)}
        self.prev_bytes = (now, cur)
        return out

    def aps(self):
        st = opn.api("/api/monit/status/get/xml").get("status", {})
        res, gw = {}, {}
        for s in st.get("service", []):
            if s.get("name", "").startswith("AP-"):
                up = s.get("status") == "0"
                icmp = s.get("icmp") if isinstance(s.get("icmp"), dict) else {}
                res[s["name"]] = {"up": up, "rtt_ms": round(float(icmp.get("responsetime", -1)) * 1000, 1) if up else None,
                                  "checked": ts(s.get("collected_sec", time.time()))}
            elif s.get("@attributes", {}).get("type") == "5":   # сам шлюз OPNsense
                sysi = s.get("system", {})
                gw = {"load1": sysi.get("load", {}).get("avg01"), "mem_pct": sysi.get("memory", {}).get("percent")}
        for name, a in res.items():  # журнал подій доступності (для R-09)
            if self.journal and name in self.ap_prev and self.ap_prev[name] != a["up"]:
                append(EVENTS, E_FIELDS, {"time": ts(time.time()), "object": name,
                                          "event": "UP" if a["up"] else "DOWN", "detail": "Monit ping"})
            self.ap_prev[name] = a["up"]
        return res, gw

    def poll(self):
        errors = []
        try:
            seg = self.sessions()
        except Exception as e:  # noqa: BLE001 — панель має працювати навіть при збої одного джерела
            seg, errors = self.state["segments"], errors + [f"сесії: {e}"]
        try:
            links = self.links()
        except Exception as e:  # noqa: BLE001
            links, errors = self.state["links"], errors + [f"трафік: {e}"]
        try:
            aps, gw = self.aps()
        except Exception as e:  # noqa: BLE001
            aps, gw, errors = self.state["aps"], self.state["gateway"], errors + [f"Monit: {e}"]
        with self.lock:
            self.state = {"updated": ts(time.time()), "updated_epoch": time.time(), "segments": seg,
                          "links": links, "aps": aps, "gateway": gw, "errors": errors}

    def loop(self):
        while True:
            self.poll()
            time.sleep(POLL_S)


MON = Monitor()


# ---------------------------------------------------------- вебпанель
def page(st):
    e = html.escape
    names = {"opt1": "STUDENTS", "opt2": "TEACHERS", "opt3": "GUESTS", "wan": "WAN (вихід в інтернет)"}
    cards = "".join(
        f"<div class=card><div class=k>{e(n)}</div><div class=v>{len(s)}</div><div class=s>активних підключень</div></div>"
        for n, s in st["segments"].items())
    aps = "".join(
        f"<tr><td>{e(n)}</td><td class={'up' if a['up'] else 'down'}>{'доступна' if a['up'] else 'НЕДОСТУПНА'}</td>"
        f"<td>{a['rtt_ms'] if a['rtt_ms'] is not None else '—'}</td><td>{e(a['checked'])}</td></tr>"
        for n, a in sorted(st["aps"].items()))
    links = "".join(
        f"<tr><td>{e(names[k])}</td><td>{v['down_mbit']}</td><td>{v['up_mbit']}</td></tr>"
        for k, v in sorted(st["links"].items(), key=lambda kv: list(names).index(kv[0])))
    sess = "".join(
        f"<tr><td>{e(n)}</td><td>{e(s['user'])}</td><td>{e(s['ip'])}</td><td>{e(s['mac'])}</td><td>{e(s['since'])}</td>"
        f"<td>{round((int(s['bytes_in']) + int(s['bytes_out'])) / 1e6, 2)}</td></tr>"
        for n, lst in st["segments"].items() for s in lst)
    journal = "".join(
        f"<tr><td>{e(r['role'])}</td><td>{e(r['user'])}</td><td>{e(r['mac'])}</td><td>{e(r['ip'])}</td>"
        f"<td>{e(r['start'])}</td><td>{e(r['end'] or 'триває')}</td></tr>"
        for r in read(JOURNAL)[-8:][::-1])
    gw = st.get("gateway") or {}
    err = "".join(f"<p class=err>Помилка джерела: {e(x)}</p>" for x in st.get("errors", []))
    return f"""<!doctype html><html lang=uk><head><meta charset=utf-8><meta http-equiv=refresh content={POLL_S}>
<title>67space — моніторинг мережі</title><style>
body{{font:14px system-ui,Segoe UI,sans-serif;margin:24px;background:#f6f7f9;color:#1d2330}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:22px 0 8px}} .muted{{color:#667}}
.cards{{display:flex;gap:12px;flex-wrap:wrap}} .card{{background:#fff;border:1px solid #dde;border-radius:8px;padding:12px 16px;min-width:150px}}
.k{{font-size:12px;color:#667}} .v{{font-size:28px;font-weight:600}} .s{{font-size:12px;color:#667}}
table{{border-collapse:collapse;background:#fff;border:1px solid #dde;min-width:520px}} td,th{{padding:6px 10px;border-bottom:1px solid #eef;text-align:left}}
th{{font-size:12px;color:#556;background:#fafbfc}} .up{{color:#17803d;font-weight:600}} .down{{color:#c42020;font-weight:700}} .err{{color:#c42020}}
</style></head><body>
<h1>67space — моніторинг мережі коворкінгу</h1>
<div class=muted>Оновлено: {e(st['updated'] or '—')} · автооновлення кожні {POLL_S} с · шлюз OPNsense: навантаження {e(str(gw.get('load1', '—')))}, пам'ять {e(str(gw.get('mem_pct', '—')))}%</div>{err}
<h2>Активні підключення за сегментами</h2><div class=cards>{cards}</div>
<h2>Стан точок доступу</h2><table><tr><th>Точка</th><th>Стан</th><th>Відгук, мс</th><th>Перевірено</th></tr>{aps}</table>
<h2>Навантаження каналу, Мбіт/с</h2><table><tr><th>Інтерфейс</th><th>До клієнтів</th><th>Від клієнтів</th></tr>{links}</table>
<h2>Сесії</h2><table><tr><th>Сегмент</th><th>Користувач</th><th>IP</th><th>Пристрій (MAC)</th><th>З</th><th>Трафік, МБ</th></tr>{sess}</table>
<h2>Журнал підключень (останні 8, зберігається {RETENTION_DAYS} днів)</h2><table><tr><th>Роль</th><th>Користувач</th><th>Пристрій</th><th>IP</th><th>Початок</th><th>Кінець</th></tr>{journal}</table>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        with MON.lock:
            st = dict(MON.state)
        if self.path.startswith("/status.json"):
            body, ctype = json.dumps(st, ensure_ascii=False, indent=1).encode(), "application/json; charset=utf-8"
        elif self.path.startswith("/journal.csv") or self.path.startswith("/events.csv"):
            path = JOURNAL if "journal" in self.path else EVENTS
            body = open(path, "rb").read() if os.path.exists(path) else b""
            ctype = "text/csv; charset=utf-8"
        else:
            body, ctype = page(st).encode(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def serve(port=PORT):
    MON.poll()
    threading.Thread(target=MON.loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    serve()
    print(f"Моніторинг 67space: http://127.0.0.1:{PORT}  (Ctrl+C — зупинити)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
