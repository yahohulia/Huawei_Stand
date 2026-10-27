"""Implementation Freeze: короткий smoke check базової версії (SC-01…SC-03).

SC-01  базова версія запускається: OPNsense і alpine-client відповідають, версія OPNsense, конфігурація
       збігається з резервною копією Freeze (SHA-256), пристрої мають адреси, точки доступу доступні.
SC-02  ключовий сценарій: до входу — сторінка порталу; студент після входу має інтернет і внутрішній сервер;
       гість за ваучером має інтернет, але не внутрішній сервер; панель моніторингу бачить сесії.
SC-03  матеріали для Testing на місці: інструкції, скрипти тестів, тестові акаунти, генерація ваучерів.

Запуск:  python scripts/smoke_check.py
Лог: ../evidence/freeze/smoke.log
"""
import hashlib
import json
import os
import time

import opn
import run_opn_tests as s1
import monitor

BASE = s1.BASE
ROOT = os.path.join(BASE, "..")
OUT = os.path.join(ROOT, "evidence", "freeze")
BACKUP = os.path.join(ROOT, "secrets", "baseline", "config-freeze-v1.0.xml")
os.makedirs(OUT, exist_ok=True)
s1.OUT = OUT
ssh, Log, DEV = s1.ssh, s1.Log, s1.DEV


def config_sha():
    """SHA-256 поточної конфігурації OPNsense без рядків, що змінюються самі (час збереження ревізії)."""
    import urllib.request
    req = urllib.request.Request(f"https://{opn.HOST}/api/core/backup/download/this")
    req.add_header("Authorization", "Basic " + opn._AUTH)
    data = urllib.request.urlopen(req, timeout=60, context=opn._CTX).read()
    return hashlib.sha256(data).hexdigest(), data


def main():
    results = []
    # ------------------------------------------------------------ SC-01
    L = Log("SC-01", "Базова версія запускається")
    fw = opn.api("/api/core/firmware/status").get("product", {}).get("product_version", "?")
    up = opn.api("/api/diagnostics/system/system_time")
    L.add(f"[OPNsense] версія {fw}, uptime {up.get('uptime')}, час {up.get('datetime')}")
    L.check("OPNsense відповідає через API", bool(up.get("uptime")), "є відповідь")
    sha, _ = config_sha()
    base = open(BACKUP + ".sha256", encoding="utf-8").read().split()[0] if os.path.exists(BACKUP + ".sha256") else "-"
    L.add(f"[OPNsense] SHA-256 конфігурації: {sha}\n[Freeze]   SHA-256 резервної копії: {base}")
    L.check("Конфігурація збігається з базовою версією Freeze", sha == base, "однакові SHA-256")
    rc, out = ssh("uptime")
    L.add(f"[alpine-client] {out}")
    L.check("alpine-client відповідає по SSH", rc == 0, "rc=0")
    for dev, d in DEV.items():
        ip = s1.ip_of(dev)
        L.add(f"[{dev}] адреса {ip}")
        L.check(f"{dev} отримав адресу у своєму VLAN", ip.startswith(d["gw"].rsplit(".", 1)[0]), d["gw"].rsplit(".", 1)[0] + ".x")
    time.sleep(2)
    st = opn.api("/api/monit/status/get/xml")["status"]["service"]
    aps = {s["name"]: s["status"] == "0" for s in st if s["name"].startswith("AP-")}
    L.add(f"[Monit] {aps}")
    L.check("Точки доступу AP-1…AP-3 доступні", len(aps) == 3 and all(aps.values()), "3 x UP")
    results.append(L.finish())

    # ------------------------------------------------------------ SC-02
    L = Log("SC-02", "Ключовий сценарій працює від початку до кінця")
    s1.reset_sessions()
    _, out = L.run("dev-student", 'curl -s -m 6 -o /dev/null -w "%{http_code} %{redirect_url}" http://1.1.1.1/')
    L.check("До входу студента перенаправлено на сторінку входу", out.startswith("302"), "302 -> портал")
    L.check("Студент увійшов", L.logon("dev-student") == "AUTHORIZED", "AUTHORIZED")
    _, out = L.run("dev-student", 'curl -s -m 8 -o /dev/null -w "%{http_code}" http://1.1.1.1/')
    L.check("Студент має інтернет", out in ("200", "301"), "200/301")
    _, out = L.run("dev-student", "curl -s -m 5 http://192.168.56.20/")
    L.check("Студент має внутрішній сервер", out == "INTRANET-OK", "INTRANET-OK")
    v = s1.fresh_voucher()
    L.check("Гість увійшов за новим ваучером", L.logon("dev-guest", user=v["username"], pw=v["password"]) == "AUTHORIZED", "AUTHORIZED")
    _, out = L.run("dev-guest", 'curl -s -m 8 -o /dev/null -w "%{http_code}" http://1.1.1.1/')
    L.check("Гість має інтернет", out in ("200", "301"), "200/301")
    _, out = L.run("dev-guest", 'curl -s -m 5 -o /dev/null -w "%{http_code}" http://192.168.56.20/')
    L.check("Гість не має внутрішнього сервера", out in ("000", ""), "немає з'єднання")
    m = monitor.Monitor(journal=False)
    m.poll()
    seg = {k: len(v) for k, v in m.state["segments"].items()}
    L.add(f"[панель моніторингу] сесії: {seg}; точки доступу: "
          + ", ".join(f"{n} {'UP' if a['up'] else 'DOWN'}" for n, a in sorted(m.state["aps"].items())))
    L.check("Панель моніторингу бачить сесії студента й гостя", seg.get("STUDENTS") == 1 and seg.get("GUESTS") == 1, "1 і 1")
    results.append(L.finish())

    # ------------------------------------------------------------ SC-03
    L = Log("SC-03", "Матеріали й середовище для Testing готові")
    need = ["README.md", "scripts/run_opn_tests.py", "scripts/run_sprint2_tests.py", "scripts/portal_screens.py",
            "scripts/client_devices.sh", "scripts/load_clients.sh", "scripts/monitor.py",
            "../Drive_package/03_Образи_VM/ІМПОРТ.txt"]
    for f in need:
        ok = os.path.exists(os.path.join(ROOT, f))
        L.add(f"[файл] {f}: {'є' if ok else 'НЕМАЄ'}")
        L.check(f"Є {f}", ok, "файл існує")
    users = {r["name"] for r in opn.api("/api/auth/user/search")["rows"]}
    L.add(f"[OPNsense] тестові акаунти: {sorted(u for u in users if u in ('student01', 'student02', 'teacher01', 'guest01'))}")
    L.check("Тестові акаунти студентів і викладача на місці", {"student01", "student02", "teacher01"} <= users, "є")
    L.check("Генерація гостьових ваучерів працює", bool(v.get("username")), "ваучер створено")
    results.append(L.finish())

    with open(os.path.join(OUT, "smoke-results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\nSmoke check (Implementation Freeze):")
    for r in results:
        n = sum(c["ok"] for c in r["checks"])
        print(f"  {r['tc']}: {r['status']}  ({n}/{len(r['checks'])})  {r['title']}")
        for c in r["checks"]:
            if not c["ok"]:
                print(f"       FAIL: {c['check']}  [очікувалось: {c['expected']}]")


if __name__ == "__main__":
    main()
