"""Sprint 1 на OPNsense: TC-04, TC-03, TC-08.
Пристрої — network namespace dev-student / dev-teacher / dev-guest у VM alpine-client
(кожен у власному VLAN). Тести виконуються по SSH, логи й журнал firewall пишуться в
../evidence/sprint1-opnsense/.
"""
import csv
import json
import os
import subprocess
import time

import opn

BASE = os.path.dirname(os.path.abspath(__file__))
KEY = os.path.join(BASE, "..", "secrets", "id_67space")
VM = "root@192.168.56.20"
OUT = os.path.join(BASE, "..", "evidence", "sprint1-opnsense")
os.makedirs(OUT, exist_ok=True)

# пристрій -> (zone id, порт порталу, шлюз, логін, пароль)
def fresh_voucher():
    """Новий ваучер на 1 годину для кожного запуску (старий міг уже закінчитись). Паролі видає лише генерація."""
    opn.api("/api/captiveportal/voucher/dropExpiredVouchers/Vouchers/67space-guests/", data={})
    v = opn.api("/api/captiveportal/voucher/generateVouchers/Vouchers",
                data={"count": "1", "validity": "3600", "expirytime": "0", "vouchergroup": "67space-guests"})[0]
    return {"username": v["username"], "password": v["password"]}


VOUCHER = fresh_voucher()
DEV = {
    "dev-student": dict(zone=0, port=8000, gw="10.67.10.1", user="student01", pw="Stud01-lab", seg="STUDENTS"),
    "dev-teacher": dict(zone=1, port=8001, gw="10.67.20.1", user="teacher01", pw="Teach01-lab", seg="TEACHERS"),
    "dev-guest":   dict(zone=2, port=8002, gw="10.67.30.1", user=VOUCHER["username"], pw=VOUCHER["password"], seg="GUESTS"),
}
INTRANET = "192.168.56.20"
INTERNET = "1.1.1.1"


def ssh(script, timeout=90):
    r = subprocess.run(["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", VM, script],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def ip_of(dev):
    return ssh(f"ip netns exec {dev} ip -4 -o addr show | grep -v ' lo ' | awk '{{print $4}}' | cut -d/ -f1")[1]


class Log:
    def __init__(self, tc, title):
        self.tc, self.title, self.lines, self.checks = tc, title, [], []
        self.add(f"=== {tc} (OPNsense): {title} ===")
        self.add(f"Час запуску: {time.strftime('%Y-%m-%d %H:%M:%S')}  |  Середовище: OPNsense 26.7 у VirtualBox + VM alpine-client (VLAN 10/20/30)")

    def add(self, s=""):
        self.lines.append(s)

    def run(self, dev, cmd, timeout=60):
        rc, out = ssh(f"ip netns exec {dev} sh -c '{cmd}'", timeout)
        self.add(f"[{dev}] $ {cmd}")
        self.add("    " + (out.replace("\n", "\n    ") if out else "(порожньо)") + f"   [rc={rc}]")
        return rc, out

    def check(self, name, ok, expected):
        self.checks.append({"check": name, "ok": bool(ok), "expected": expected})
        self.add(f"  -> {'PASS' if ok else 'FAIL'}: {name}  (очікується: {expected})")

    def logon(self, dev, user=None, pw=None, zone=None, port=None, gw=None):
        d = DEV[dev]
        user, pw = user or d["user"], pw or d["pw"]
        zone, port, gw = zone if zone is not None else d["zone"], port or d["port"], gw or d["gw"]
        rc, out = ssh(f"ip netns exec {dev} curl -s -m 8 -d 'user={user}&password={pw}' "
                      f"http://{gw}:{port}/api/captiveportal/access/logon/{zone}/")
        try:
            state = json.loads(out).get("clientState", "?")
        except ValueError:
            state = "no-json"
        self.add(f"[{dev}] вхід у зону {zone} як {user} -> clientState={state}")
        return state

    def finish(self):
        status = "PASS" if all(c["ok"] for c in self.checks) else "FAIL"
        self.add(f"\nПІДСУМОК {self.tc}: {status} ({sum(c['ok'] for c in self.checks)}/{len(self.checks)} перевірок)")
        with open(os.path.join(OUT, f"{self.tc}.log"), "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines) + "\n")
        return {"tc": self.tc, "title": self.title, "status": status, "checks": self.checks}


def reset_sessions():
    for z in (0, 1, 2):
        for s in opn.api(f"/api/captiveportal/session/list/{z}") or []:
            opn.api(f"/api/captiveportal/session/disconnect/{z}", data={"sessionId": s["sessionId"]})
    time.sleep(2)


def start_listeners():
    for dev in DEV:  # по HTTP-серверу на :8000 у кожному "пристрої"
        ssh(f"ip netns exec {dev} sh -c 'mkdir -p /tmp/www-{dev}; echo {dev} > /tmp/www-{dev}/index.html; "
            f"pgrep -f \"httpd -p 8000 -h /tmp/www-{dev}\" >/dev/null || busybox-extras httpd -p 8000 -h /tmp/www-{dev}'")


def fw_blocks(since_ts, src_prefix):
    rows = opn.api("/api/diagnostics/firewall/log/?limit=2000") or []
    return [r for r in rows if r.get("action") == "block" and r.get("src", "").startswith(src_prefix)
            and r.get("__timestamp__", "") >= since_ts]


def tc04():
    L = Log("TC-04", "Captive Portal: доступ до та після входу (R-04)")
    reset_sessions()
    L.add("\nПередумова: пристрої в VLAN своїх сегментів, вхід не виконано.")
    for dev, d in DEV.items():
        L.add(f"\n-- {dev} ({d['seg']})")
        _, out = L.run(dev, f'curl -s -m 6 -o /dev/null -w "%{{http_code}} %{{redirect_url}}" http://93.184.216.34/')
        L.check(f"{dev}: до входу HTTP перенаправлено на портал", out.startswith("302") and f"{d['gw']}:{d['port']}" in out,
                f"302 -> {d['gw']}:{d['port']}")
        rc, _ = L.run(dev, f"ping -c1 -W2 {INTERNET}")
        L.check(f"{dev}: до входу ping в інтернет закритий", rc != 0, "немає відповіді")
        _, out = L.run(dev, f'curl -sk -m 5 -o /dev/null -w "%{{http_code}}" https://{INTERNET}/')
        L.check(f"{dev}: до входу HTTPS в інтернет закритий", out in ("000", ""), "немає з'єднання")

    L.add("\n-- Негативні входи")
    s = L.logon("dev-student", pw="wrong-password")
    L.check("Хибний пароль не пускає", s == "NOT_AUTHORIZED", "NOT_AUTHORIZED")
    s = L.logon("dev-guest", user="student01", pw="Stud01-lab")
    L.check("Акаунт студента не входить у гостьову зону", s == "NOT_AUTHORIZED", "NOT_AUTHORIZED")
    s = L.logon("dev-student", user="teacher01", pw="Teach01-lab")
    L.check("Акаунт викладача не входить у зону студентів", s == "NOT_AUTHORIZED", "NOT_AUTHORIZED")
    s = L.logon("dev-student", user=VOUCHER["username"], pw=VOUCHER["password"])
    L.check("Гостьовий ваучер не входить у зону студентів", s == "NOT_AUTHORIZED", "NOT_AUTHORIZED")
    s = L.logon("dev-guest", user="nonexistent", pw="xxxxxx")
    L.check("Неіснуючий ваучер не пускає", s == "NOT_AUTHORIZED", "NOT_AUTHORIZED")
    _, out = L.run("dev-student", f'curl -s -m 5 -o /dev/null -w "%{{http_code}}" http://{INTERNET}/')
    L.check("Після невдалих входів інтернет усе ще закритий (редирект, не вміст)", out == "302", "302")

    L.add("\n-- Успішні входи")
    for dev, d in DEV.items():
        s = L.logon(dev)
        L.check(f"{dev}: вхід успішний ({d['seg']})", s == "AUTHORIZED", "AUTHORIZED")
        rc, out = L.run(dev, f"curl -s -m 8 -o /dev/null -w \"%{{http_code}}\" http://{INTERNET}/")
        L.check(f"{dev}: після входу є доступ в інтернет", out in ("200", "301", "302") and not out.startswith("000"), "відповідь сайту")
        rc, _ = L.run(dev, f"ping -c1 -W3 {INTERNET}")
        L.check(f"{dev}: після входу ping в інтернет працює", rc == 0, "є відповідь")
    sessions = [s for z in (0, 1, 2) for s in (opn.api(f"/api/captiveportal/session/list/{z}") or [])]
    L.add("\n[OPNsense] активні сесії порталу:")
    for s in sessions:
        L.add(f"    зона {s.get('zoneid')}  {s.get('userName')}  {s.get('ipAddress')}  via {s.get('authenticated_via')}")
    L.check("Портал веде 3 активні сесії (по одній на сегмент)", len(sessions) >= 3, ">= 3")
    return L.finish()


def tc03():
    L = Log("TC-03", "Рольова сегментація: Студенти / Викладачі / Гості (R-03)")
    start_listeners()
    for dev in DEV:
        if L.logon(dev) != "AUTHORIZED":
            L.add(f"  (повторний вхід {dev} не вдався)")
    ips = {d: ip_of(d) for d in DEV}
    L.add("\n-- Крок 1. Адреси, отримані по DHCP від OPNsense")
    for dev, d in DEV.items():
        L.check(f"{dev}: адреса з підмережі {d['gw'].rsplit('.', 1)[0]}.0/24", ips[dev].startswith(d["gw"].rsplit(".", 1)[0]), d["gw"].rsplit(".", 1)[0] + ".x")
        L.run(dev, "ip -4 -o addr show | grep -v \" lo \" | awk \"{print \\$2, \\$4}\"; ip route | head -2")
    t0 = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3600 * 0))  # локальний час VM може відрізнятись
    started = opn.api("/api/diagnostics/firewall/log/?limit=1")
    t0 = started[0]["__timestamp__"] if started else t0

    L.add("\n-- Крок 2. Спроби дістатися пристроїв інших сегментів (ping + TCP :8000 + nmap)")
    for src in DEV:
        for dst in DEV:
            if src == dst:
                continue
            rc, _ = L.run(src, f"ping -c2 -W1 {ips[dst]}")
            L.check(f"{src} -> {dst} ({ips[dst]}): ping заблоковано", rc != 0, "100% втрат")
            _, out = L.run(src, f'curl -s -m 4 -o /dev/null -w "%{{http_code}}" http://{ips[dst]}:8000/')
            L.check(f"{src} -> {dst}: TCP :8000 недоступний", out in ("000", ""), "немає з'єднання")
        gws = " ".join(d["gw"] for k, d in DEV.items() if k != src)
        rc, _ = L.run(src, f'curl -sk -m 4 -o /dev/null -w "%{{http_code}}" https://{[d["gw"] for k, d in DEV.items() if k != src][0]}/')
        L.run(src, f"nmap -Pn -n -p 8000 --max-retries 1 --host-timeout 12s " + " ".join(ips[d] for d in DEV if d != src)
              + " | grep -E \"Nmap scan|8000\"", 60)
    L.add("\n-- Крок 3. Контроль: легітимний доступ працює")
    for dev in DEV:
        _, out = L.run(dev, f'curl -s -m 8 -o /dev/null -w "%{{http_code}}" http://{INTERNET}/')
        L.check(f"{dev}: інтернет доступний", out in ("200", "301", "302"), "відповідь сайту")
    for dev in ("dev-student", "dev-teacher"):
        _, out = L.run(dev, f"curl -s -m 5 http://{INTRANET}/")
        L.check(f"{dev}: внутрішні ресурси доступні", out == "INTRANET-OK", "INTRANET-OK")

    L.add("\n-- Evidence: записи блокування у журналі firewall OPNsense")
    time.sleep(2)
    for prefix, seg in (("10.67.10.", "STUDENTS"), ("10.67.20.", "TEACHERS"), ("10.67.30.", "GUESTS")):
        blocks = fw_blocks(t0, prefix)
        L.add(f"[firewall log] block, джерело {prefix}x ({seg}): {len(blocks)} записів")
        for r in blocks[:6]:
            L.add(f"    {r['__timestamp__']}  {r['interface']}  {r['src']} -> {r['dst']}  proto={r['protoname']} dport={r.get('dstport', '-')}")
        L.check(f"Firewall зафіксував блокування від {seg}", len(blocks) > 0, "є записи block")
    rules = [r for r in opn.api("/api/firewall/filter/searchRule")["rows"] if "67space" in r.get("description", "")]
    with open(os.path.join(OUT, "firewall-rules.txt"), "w", encoding="utf-8") as f:
        for r in sorted(rules, key=lambda r: int(r.get("sequence", 0))):
            f.write(f"{r['sequence']:>5} {r['interface']:5} {r['action']:6} dst={r['destination_net']:20} {r['description']}\n")
    L.add("\n[OPNsense] правила firewall збережено в firewall-rules.txt")
    return L.finish()


def tc08():
    L = Log("TC-08", "Ізоляція гостьового сегмента від внутрішніх ресурсів (R-08)")
    L.logon("dev-guest")
    L.logon("dev-student")
    latest = opn.api("/api/diagnostics/firewall/log/?limit=1")
    t0 = latest[0]["__timestamp__"] if latest else "1970"
    L.add("\nПередумова: гість авторизований у GUESTS, внутрішній сервер університету — " + INTRANET)
    L.add("\n-- Крок 1. Контроль: сервер взагалі працює (з боку студента)")
    _, out = L.run("dev-student", f"curl -s -m 5 http://{INTRANET}/")
    L.check("Студент бачить внутрішній сервер (сервер живий)", out == "INTRANET-OK", "INTRANET-OK")
    L.add("\n-- Крок 2. Спроби гостя дістатися внутрішніх ресурсів")
    _, out = L.run("dev-guest", f'curl -s -m 5 -o /dev/null -w "%{{http_code}}" http://{INTRANET}/')
    L.check("HTTP до внутрішнього сервера заблоковано", out in ("000", ""), "немає з'єднання")
    rc, _ = L.run("dev-guest", f"ping -c2 -W1 {INTRANET}")
    L.check("ping внутрішнього сервера заблоковано", rc != 0, "100% втрат")
    _, out = L.run("dev-guest", f"nmap -Pn -n -p 22,80,443,8080 --max-retries 1 --host-timeout 15s {INTRANET}")
    L.check("Сканування портів: жоден порт не open", " open " not in out, "усі filtered")
    for t, name in (("192.168.56.10", "керування OPNsense (LAN)"), ("10.67.10.1", "шлюз студентського VLAN"), ("10.67.20.1", "шлюз VLAN викладачів")):
        _, out = L.run("dev-guest", f'curl -sk -m 4 -o /dev/null -w "%{{http_code}}" https://{t}/')
        L.check(f"Гість не відкриває {name} ({t})", out in ("000", ""), "немає з'єднання")
    L.add("\n-- Крок 3. Контроль: гість має інтернет")
    _, out = L.run("dev-guest", f'curl -s -m 8 -o /dev/null -w "%{{http_code}}" http://{INTERNET}/')
    L.check("Гість: інтернет працює", out in ("200", "301", "302"), "відповідь сайту")
    time.sleep(2)
    L.add("\n-- Evidence: журнал firewall")
    blocks = [r for r in fw_blocks(t0, "10.67.30.") if r["dst"] in (INTRANET, "192.168.56.10", "10.67.10.1", "10.67.20.1")]
    L.add(f"[firewall log] block від гостя до внутрішніх адрес: {len(blocks)} записів")
    for r in blocks[:8]:
        L.add(f"    {r['__timestamp__']}  {r['interface']}  {r['src']} -> {r['dst']}  proto={r['protoname']} dport={r.get('dstport', '-')}")
    L.check("Firewall зафіксував блокування гостьового трафіку до внутрішніх адрес", len(blocks) > 0, "є записи block")
    return L.finish()


if __name__ == "__main__":
    results = [tc04(), tc03(), tc08()]
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\nЗведення (OPNsense):")
    for r in results:
        n = sum(c["ok"] for c in r["checks"])
        print(f"  {r['tc']}: {r['status']}  ({n}/{len(r['checks'])})  {r['title']}")
        for c in r["checks"]:
            if not c["ok"]:
                print(f"       FAIL: {c['check']}  [очікувалось: {c['expected']}]")
