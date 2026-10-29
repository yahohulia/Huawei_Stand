"""Sprint 2 на OPNsense: TC-05 (моніторинг + журнал підключень), TC-07 (ліміти гостьової сесії),
TC-02 (змодельоване пікове навантаження), TC-10 (повторне розгортання за інструкцією + регрес).
Логи, графік і знімки панелі пишуться в ../evidence/sprint2-opnsense/.

Запуск:  python scripts/run_sprint2_tests.py            (усі тести, ~15 хв)
         python scripts/run_sprint2_tests.py TC-07 TC-05  (вибрані)
Передумова: стенд Sprint 1 + opnsense_setup_sprint2.py, у alpine-client запущено client_devices.sh.
"""
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

import opn
import run_opn_tests as s1
import monitor
import opnsense_setup_sprint2 as cfg

OUT = os.path.join(s1.BASE, "..", "evidence", "sprint2-opnsense")
os.makedirs(OUT, exist_ok=True)
s1.OUT = OUT                      # Log.finish() пише логи сюди
ssh, Log, DEV = s1.ssh, s1.Log, s1.DEV
SPEED_URL = "https://speed.cloudflare.com/__down?bytes="
MIN_MBIT = 2.0                    # поріг R-06 у моделі (реальне значення TBD, Q-02)
N_PEAK = (30, 15, 15)             # пікове навантаження у моделі: студенти, викладачі, гості (N_peak TBD, Q-02)
LOAD_S = 180                      # тривалість навантаження (у сценарії 30 хв, у моделі скорочено)


def zone_uuid(name):
    return next(r["uuid"] for r in opn.api("/api/captiveportal/settings/searchZones")["rows"]
                if r["description"] == f"67space {name}")


def sessions(zone):
    r = opn.api(f"/api/captiveportal/session/list/{zone}")
    return r if isinstance(r, list) else []


def portal_log(n=60):
    r = opn.api("/api/diagnostics/log/core/portalauth", data={"current": 1, "rowCount": n, "searchPhrase": ""})
    return r.get("rows", [])


def get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8")


# ======================================================================= TC-05
def tc05():
    L = Log("TC-05", "Моніторинг мережі (R-05) і журнал підключень (R-14)")
    L.add("Інструмент: scripts/monitor.py — панель http://127.0.0.1:8067, опитування OPNsense API кожні "
          f"{monitor.POLL_S} с; стан точок доступу — Monit на OPNsense (ping кожні {cfg.MONIT_INTERVAL_S} с).")
    L.add("Інтервал оновлення в моделі: зміна стану обладнання має з'явитись на панелі не пізніше 60 с (TBD у R-05).")
    s1.reset_sessions()
    srv = monitor.serve(8067)
    try:
        L.add("\n-- Крок 1. Активні сесії за сегментами")
        for dev in DEV:
            L.logon(dev)
        time.sleep(monitor.POLL_S + 2)
        st = json.loads(get("http://127.0.0.1:8067/status.json"))
        for zone, (seg, _) in monitor.SEGMENTS.items():
            api_n, panel_n = len(sessions(zone)), len(st["segments"].get(seg, []))
            L.add(f"[панель] {seg}: {panel_n} підключень; [OPNsense API] {api_n}")
            L.check(f"Панель показує кількість підключень {seg}", panel_n == api_n == 1, "1 = 1")

        L.add("\n-- Крок 2. Навантаження каналу (студент завантажує файл із внутрішнього сервера)")
        dl = threading.Thread(target=ssh, args=(
            "ip netns exec dev-student sh -c 'end=$(( $(date +%s) + 30 )); n=0; while [ $(date +%s) -lt $end ]; do "
            "curl -s -o /dev/null http://192.168.56.20/1mb.bin && n=$((n+1)); done; echo $n'", 60))
        dl.start()
        peak = 0.0
        for _ in range(3):
            time.sleep(monitor.POLL_S)
            st = json.loads(get("http://127.0.0.1:8067/status.json"))
            peak = max(peak, st["links"].get("opt1", {}).get("down_mbit", 0))
        L.add(f"[панель] STUDENTS, до клієнтів: пік {peak} Мбіт/с; інтерфейси на панелі: {sorted(st['links'])}")
        L.check("Панель показує навантаження каналу за інтерфейсами", set(st["links"]) >= {"opt1", "opt2", "opt3", "wan"}, "4 інтерфейси")
        L.check("Навантаження STUDENTS видно під час завантаження", peak > 0.5, "> 0.5 Мбіт/с")

        L.add("\n-- Крок 3. Стан точок доступу й симуляція збою AP-2")
        st = json.loads(get("http://127.0.0.1:8067/status.json"))
        L.add("[панель] " + ", ".join(f"{n}: {'UP' if a['up'] else 'DOWN'}" for n, a in sorted(st["aps"].items())))
        L.check("Усі 3 точки доступу доступні до збою", len(st["aps"]) == 3 and all(a["up"] for a in st["aps"].values()), "3 x UP")
        open(os.path.join(OUT, "dashboard-normal.html"), "w", encoding="utf-8").write(get("http://127.0.0.1:8067/"))
        ssh("ip netns exec ap-2 ip link set mgmt0 down")
        t0 = time.time()
        L.add(f"[{time.strftime('%H:%M:%S')}] AP-2 вимкнено (ip netns exec ap-2 ip link set mgmt0 down)")
        detected = None
        while time.time() - t0 < 120:
            st = json.loads(get("http://127.0.0.1:8067/status.json"))
            if not st["aps"].get("AP-2", {}).get("up", True):
                detected = round(time.time() - t0)
                break
            time.sleep(2)
        L.add(f"[панель] AP-2 показано як НЕДОСТУПНА через {detected} с")
        L.check("Збій AP-2 видно на панелі не пізніше 60 с", detected is not None and detected <= 60, "<= 60 с")
        L.check("Інші точки доступу лишаються доступними", all(st["aps"][n]["up"] for n in ("AP-1", "AP-3")), "AP-1, AP-3 UP")
        open(os.path.join(OUT, "dashboard-ap2-down.html"), "w", encoding="utf-8").write(get("http://127.0.0.1:8067/"))
        ssh("ip netns exec ap-2 ip link set mgmt0 up")
        t0, back = time.time(), None
        while time.time() - t0 < 120:
            st = json.loads(get("http://127.0.0.1:8067/status.json"))
            if st["aps"].get("AP-2", {}).get("up"):
                back = round(time.time() - t0)
                break
            time.sleep(2)
        L.add(f"[панель] AP-2 знову доступна через {back} с після ввімкнення")
        L.check("Відновлення AP-2 видно на панелі", back is not None and back <= 60, "<= 60 с")
        ev = monitor.read(monitor.EVENTS)[-2:]
        for e in ev:
            L.add(f"[events.csv] {e['time']}  {e['object']}  {e['event']}")
        L.check("Журнал подій зафіксував DOWN і UP для AP-2", [e["event"] for e in ev] == ["DOWN", "UP"]
                and all(e["object"] == "AP-2" for e in ev), "DOWN, UP")

        L.add("\n-- Крок 4. Журнал підключень (R-14): завершення сесії викладача")
        sid = sessions(1)[0]["sessionId"]
        opn.api("/api/captiveportal/session/disconnect/1", data={"sessionId": sid})
        L.add("[OPNsense] сесію teacher01 завершено адміністратором")
        time.sleep(monitor.POLL_S + 3)
        row = next((r for r in monitor.read(monitor.JOURNAL) if r["session_id"] == sid), None)
        L.add(f"[connections.csv] {row}")
        L.check("У журналі є роль, користувач, пристрій (MAC), час початку й кінця",
                bool(row) and row["role"] == "викладач" and row["user"] == "teacher01"
                and row["mac"] == "02:67:00:00:20:01" and row["start"] and row["end"], "усі поля заповнені")
        auth = [r for r in portal_log() if "AUTH teacher01" in r["line"]]
        L.add(f"[OPNsense, журнал порталу] {auth[0]['timestamp']} {auth[0]['line'].strip()}" if auth else "[журнал порталу] записів немає")
        L.check("Вхід зафіксовано і в журналі порталу OPNsense", bool(auth), "AUTH teacher01")
        keep = opn.api("/api/syslog/settings/get")["syslog"]["general"]["maxpreserve"]
        L.add(f"[OPNsense] журнали зберігаються {keep} днів; connections.csv — {monitor.RETENTION_DAYS} днів (TBD, OQ-6)")
        L.check("Строк зберігання журналів налаштовано", keep == str(cfg.LOG_DAYS) and monitor.RETENTION_DAYS == cfg.LOG_DAYS,
                f"{cfg.LOG_DAYS} днів")
        L.logon("dev-teacher")
    finally:
        srv.shutdown()
    return L.finish()


# ======================================================================= TC-07
def tc07():
    L = Log("TC-07", "Ліміти гостьової сесії: швидкість і тривалість (R-07)")
    L.add(f"Налаштування моделі: швидкість гостя {cfg.GUEST_MBIT} Мбіт/с на пристрій (поріг R-06 у моделі {MIN_MBIT}), "
          f"сесія до {cfg.GUEST_HARD_MIN} хв, без активності до {cfg.GUEST_IDLE_MIN} хв.")
    L.add("Щоб не чекати 60 хв, на час тесту hard timeout зони GUESTS скорочено до 2 хв, після тесту повернено.")
    uuid = zone_uuid("GUESTS")
    v = s1.fresh_voucher()
    s1.reset_sessions()
    opn.api(f"/api/captiveportal/settings/setZone/{uuid}", data={"zone": {"hardtimeout": "2"}})
    opn.api("/api/captiveportal/service/reconfigure", data={})
    try:
        L.add("\n-- Крок 1. Вхід гостя й перевірка швидкості")
        st = L.logon("dev-guest", user=v["username"], pw=v["password"])
        t_login = time.time()
        L.check("Гість увійшов за ваучером", st == "AUTHORIZED", "AUTHORIZED")
        L.logon("dev-student")    # для порівняння: студент без ліміту
        speeds = {}
        for dev in ("dev-guest", "dev-student"):
            _, out = L.run(dev, f'curl -s -m 40 -o /dev/null -w "%{{speed_download}}" "{SPEED_URL}3000000"', 60)
            try:
                speeds[dev] = round(float(out) * 8 / 1e6, 2)
            except ValueError:
                speeds[dev] = 0.0
        L.add(f"Швидкість завантаження: гість {speeds['dev-guest']} Мбіт/с, студент (без ліміту) {speeds['dev-student']} Мбіт/с")
        L.check("Швидкість гостя не перевищує ліміт", speeds["dev-guest"] <= cfg.GUEST_MBIT * 1.05, f"<= {cfg.GUEST_MBIT} Мбіт/с")
        L.check("Швидкість гостя не нижча за поріг R-06 моделі", speeds["dev-guest"] >= MIN_MBIT, f">= {MIN_MBIT} Мбіт/с")
        L.check("Ліміт діє лише на гостей (студент швидший)", speeds["dev-student"] > speeds["dev-guest"], "студент > гість")

        L.add("\n-- Крок 2. Сесія триває до тайм-ауту")
        ended = None
        while time.time() - t_login < 300:
            if not sessions(2):
                ended = round(time.time() - t_login)
                break
            time.sleep(5)
        L.add(f"[OPNsense] сесію гостя закрито через {ended} с після входу (hard timeout 2 хв)")
        L.check("Сесію закрито автоматично після тайм-ауту", ended is not None and 110 <= ended <= 240, "≈ 120 с (+ цикл перевірки)")
        _, out = L.run("dev-guest", 'curl -s -m 6 -o /dev/null -w "%{http_code} %{redirect_url}" http://1.1.1.1/')
        L.check("Після тайм-ауту гостя знову перенаправлено на портал", out.startswith("302"), "302 -> портал")
        log = [r for r in portal_log(30) if v["username"] in r["line"] or "limit" in r["line"] or "timeout" in r["line"]]
        for r in log[:4]:
            L.add(f"[OPNsense, журнал порталу] {r['timestamp']} {r['line'].strip()}")
        L.check("Закриття сесії видно в журналі порталу", any("AUTH " + v["username"] in r["line"] for r in log)
                and any("limit" in r["line"] or "timeout" in r["line"] for r in log), "AUTH + закриття")

        L.add("\n-- Крок 3. Повторний вхід")
        st = L.logon("dev-guest", user=v["username"], pw=v["password"])
        L.check("Після повторного входу доступ відновлено", st == "AUTHORIZED", "AUTHORIZED")
    finally:
        opn.api(f"/api/captiveportal/settings/setZone/{uuid}", data={"zone": {"hardtimeout": str(cfg.GUEST_HARD_MIN)}})
        opn.api("/api/captiveportal/service/reconfigure", data={})
        z = opn.api(f"/api/captiveportal/settings/getZone/{uuid}")["zone"]
        L.add(f"\n[OPNsense] hard timeout зони GUESTS повернено: {z['hardtimeout']} хв, idle {z['idletimeout']} хв")
    L.check("Робоче значення тайм-ауту відновлено", z["hardtimeout"] == str(cfg.GUEST_HARD_MIN), f"{cfg.GUEST_HARD_MIN} хв")
    return L.finish()


# ======================================================================= TC-02
def load_accounts(n_s, n_t, n_g):
    """Окремі акаунти/ваучери для кожного клієнта: зона пускає одну сесію на акаунт."""
    groups = {r["name"]: r["gid"] for r in opn.api("/api/auth/group/search")["rows"]}
    drop_load_accounts()
    creds = {}
    for prefix, n, grp in (("s", n_s, "v67-students"), ("t", n_t, "v67-teachers")):
        for i in range(1, n + 1):
            user = f"load-{prefix}{i:02d}"
            opn.api("/api/auth/user/add", data={"user": {"name": user, "password": f"Load-{i:02d}-lab", "scope": "user",
                                                          "descr": "67space TC-02 load", "group_memberships": groups[grp]}})
            creds[f"ld-{prefix}{i:02d}"] = (user, f"Load-{i:02d}-lab")
    vs = opn.api("/api/captiveportal/voucher/generateVouchers/Vouchers",
                 data={"count": str(n_g), "validity": "3600", "expirytime": "0", "vouchergroup": "67space-load"})
    for i, v in enumerate(vs, start=1):
        creds[f"ld-g{i:02d}"] = (v["username"], v["password"])
    return creds


def drop_load_accounts():
    for r in opn.api("/api/auth/user/search")["rows"]:
        if r["name"].startswith("load-"):
            opn.api(f"/api/auth/user/del/{r['uuid']}", data={})
    opn.api("/api/captiveportal/voucher/dropVoucherGroup/Vouchers/67space-load", data={})


def tc02():
    n_s, n_t, n_g = N_PEAK
    total = n_s + n_t + n_g
    L = Log("TC-02", "Змодельоване пікове навантаження (R-02)")
    L.add(f"Модель: N_peak = {total} пристроїв ({n_s} студентів, {n_t} викладачів, {n_g} гостей), навантаження {LOAD_S} с "
          f"(у сценарії — 30 хв). Значення N_peak і порогу швидкості — припущення моделі (TBD, Q-02).")
    L.add("Клієнти — network namespace у VM alpine-client, кожен із власною MAC, адресою по DHCP і власним входом у портал.")
    creds = load_accounts(n_s, n_t, n_g)
    ssh("cd /var/www/intranet; [ -f 1mb.bin ] || dd if=/dev/urandom of=1mb.bin bs=1k count=1024 2>/dev/null; "
        "[ -f 100k.bin ] || dd if=/dev/urandom of=100k.bin bs=1k count=100 2>/dev/null")
    samples, stop = [], threading.Event()
    mon = monitor.Monitor(journal=False)   # лише графік; журнал веде запущена панель

    def sampler():
        while not stop.is_set():
            mon.poll()
            st = mon.state
            samples.append({"t": time.time(), **{seg: len(v) for seg, v in st["segments"].items()},
                            **{k: v["down_mbit"] + v["up_mbit"] for k, v in st["links"].items()}})
            stop.wait(monitor.POLL_S)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    try:
        L.add("\n-- Крок 1. Підключення пристроїв (DHCP), хвилями по 10")
        t0 = time.time()
        # мітка — останній запис журналу DHCP до старту (за годинником самого OPNsense, а не цього ПК)
        last = opn.api("/api/diagnostics/log/core/dnsmasq", data={"current": 1, "rowCount": 1, "searchPhrase": ""}).get("rows", [])
        mark = last[0]["timestamp"] if last else "1970"
        _, out = ssh(f"sh /root/load_clients.sh up {n_s} {n_t} {n_g}", 900)
        got = dict(line.split()[:2] for line in out.splitlines() if line.startswith("ld-"))
        fail = [ns for ns, ip in got.items() if ip == "DHCP-FAIL"]
        retried = [line.split()[0] for line in out.splitlines() if "retry" in line and "DHCP-FAIL" not in line]
        L.add(f"Отримали адресу: {len(got) - len(fail)} з {total} за {round(time.time() - t0)} с; без адреси: {fail or 'немає'}")
        L.add(f"З повторного запиту клієнта (як робить справжній пристрій): {len(retried)} {retried if retried else ''}")
        L.check("Кожен пристрій отримав адресу (0 відмов DHCP)", len(got) == total and not fail, f"{total}/{total}")
        offers = [r for r in opn.api("/api/diagnostics/log/core/dnsmasq", data={"current": 1, "rowCount": 2000, "searchPhrase": "02:67:01"}).get("rows", [])
                  if r["timestamp"] >= mark]
        acks = {r["line"].split()[-1] for r in offers if r["line"].strip().startswith("DHCPACK")}
        L.add(f"[OPNsense, журнал DHCP] DHCPACK для {len(acks)} різних пристроїв навантаження")
        L.check("Шлюз видав адресу кожному пристрою (DHCPACK)", len(acks) >= total, f">= {total}")

        L.add("\n-- Крок 2. Вхід у captive portal усіх пристроїв (хвилями по 5)")
        zone = {"s": (0, "10.67.10.1", 8000), "t": (1, "10.67.20.1", 8001), "g": (2, "10.67.30.1", 8002)}
        lines = []
        for ns, (u, p) in creds.items():
            z, gw, port = zone[ns[3]]
            lines.append(f"{ns} {z} {gw} {port} {u} {p}")
        # хвилі по 5: OPNsense перевіряє пароль повільним хешем argon2, а клієнт чекає відповіді до 60 с
        script = ("mkdir -p /tmp/ld; rm -f /tmp/ld/*.login; k=0; while read ns z gw port u p; do "
                  "(ip netns exec $ns curl -s -m 60 -d \"user=$u&password=$p\" http://$gw:$port/api/captiveportal/access/logon/$z/ "
                  "> /tmp/ld/$ns.login) & k=$((k+1)); [ $((k % 5)) -eq 0 ] && wait; done; wait; "
                  "grep -l '\"clientState\":\"AUTHORIZED\"' /tmp/ld/*.login | wc -l")
        # байти, а не текст: інакше Windows додає \r до кожного рядка й паролі стають хибними
        r = subprocess.run(["ssh", "-i", s1.KEY, "-o", "BatchMode=yes", s1.VM, script],
                           input=("\n".join(lines) + "\n").encode(), capture_output=True, timeout=600)
        ok_login = int((r.stdout.decode().strip() or "0").splitlines()[-1])
        L.add(f"Успішних входів: {ok_login} з {total}")
        L.check("Кожен пристрій пройшов авторизацію (0 відмов входу)", ok_login == total, f"{total}/{total}")
        per_zone = {z: len([s for s in sessions(z) if s["macAddress"].startswith("02:67:01")]) for z in (0, 1, 2)}
        L.add(f"[OPNsense] сесії навантаження за зонами: STUDENTS {per_zone[0]}, TEACHERS {per_zone[1]}, GUESTS {per_zone[2]}")
        L.check("Портал одночасно веде всі сесії", per_zone == {0: n_s, 1: n_t, 2: n_g}, f"{n_s}/{n_t}/{n_g}")

        L.add(f"\n-- Крок 3. Одночасна робота всіх пристроїв {LOAD_S} с")
        L.add("Усі пристрої працюють як користувачі: кожні ~5 с відкривають сторінку в інтернеті, студенти й викладачі "
              "по черзі беруть і файл 100 КБ із внутрішнього сервера. Тим часом 3 контрольні пристрої (ld-s01, ld-t01, ld-g01) "
              "безперервно міряють швидкість: студент і викладач — файл 1 МБ із внутрішнього сервера через шлюз, гість — "
              "500 КБ з інтернету (як у TC-06: замір на кількох пристроях під час навантаження).")
        load = (f"rm -f /tmp/ld/*.res; end=$(( $(date +%s) + {LOAD_S} )); "
                "for ns in $(ip netns list | awk '/^ld-/{print $1}'); do ( while [ $(date +%s) -lt $end ]; do "
                "case $ns in "
                "ld-s01|ld-t01) ip netns exec $ns curl -s -m 30 -o /dev/null -w 'dl %{http_code} %{speed_download}\\n' http://192.168.56.20/1mb.bin >> /tmp/ld/$ns.res; sleep 2 ;; "
                f"ld-g01) ip netns exec $ns curl -s -m 30 -o /dev/null -w 'dl %{{http_code}} %{{speed_download}}\\n' '{SPEED_URL}500000' >> /tmp/ld/$ns.res; sleep 2 ;; "
                "ld-g*) ip netns exec $ns curl -s -m 20 -o /dev/null -w 'page %{http_code} 0\\n' http://1.1.1.1/ >> /tmp/ld/$ns.res; sleep 5 ;; "
                "*) ip netns exec $ns curl -s -m 20 -o /dev/null -w 'page %{http_code} 0\\n' http://1.1.1.1/ >> /tmp/ld/$ns.res; sleep 5; "
                "ip netns exec $ns curl -s -m 20 -o /dev/null -w 'page %{http_code} 0\\n' http://192.168.56.20/100k.bin >> /tmp/ld/$ns.res; sleep 5 ;; "
                "esac; done ) & done; wait; for f in /tmp/ld/*.res; do sed \"s|^|$(basename $f .res) |\" $f; done")
        _, out = ssh(load, LOAD_S + 420)
        rows = [line.split() for line in out.splitlines() if line.startswith("ld-") and len(line.split()) == 4]
        # 302 — це редирект на портал (доступу немає), тому успіх лише 200 / 301 (1.1.1.1 переадресовує на https)
        bad = [r for r in rows if r[2] not in ("200", "301")]
        active = {r[0] for r in rows if r[2] in ("200", "301")}
        with open(os.path.join(OUT, "TC-02-requests.txt"), "w", encoding="utf-8") as f:   # для розбору відмов
            f.write("\n".join(" ".join(r) for r in rows) + "\n")
        L.add(f"Запитів: {len(rows)}, невдалих: {len(bad)}" + (f" (приклади: {bad[:5]})" if bad else ""))
        L.add(f"Пристроїв з успішними запитами: {len(active)} з {total}")
        L.check("Усі пристрої працюють у мережі одночасно", len(active) == total, f"{total}/{total}")
        L.check("Жоден запит не отримав відмову під час навантаження", rows and not bad, "0 невдалих")
        sp = {"s": [], "t": [], "g": []}
        for ns, kind, code, speed in rows:
            if kind == "dl" and code == "200":
                sp[ns[3]].append(float(speed) * 8 / 1e6)
        for k, name in (("s", "студент ld-s01"), ("t", "викладач ld-t01"), ("g", "гість ld-g01")):
            v = sp[k]
            if v:
                L.add(f"Швидкість, {name}: вимірів {len(v)}, медіана {statistics.median(v):.2f}, мін {min(v):.2f}, макс {max(v):.2f} Мбіт/с")
        allv = sp["s"] + sp["t"] + sp["g"]
        share = sum(x >= MIN_MBIT for x in allv) / len(allv) if allv else 0
        st_v = sp["s"] + sp["t"]
        st_share = sum(x >= MIN_MBIT for x in st_v) / len(st_v) if st_v else 0
        L.add(f"Контрольні пристрої: {share:.0%} вимірів не нижче {MIN_MBIT} Мбіт/с; лише студент і викладач — "
              f"{st_share:.0%} (інформаційно, не критерій моделі; гість обмежений 3 Мбіт/с і ділить вихід в інтернет)")
        L.add("GAP моделі: пропускна здатність моделі — один віртуальний шлюз (~25–30 Мбіт/с через шлюз, ~10–15 Мбіт/с в "
              "інтернет через NAT VirtualBox на всі пристрої) — не відповідає реальному каналу коворкінгу, тож поріг "
              "швидкості R-06 для N_peak на моделі не перевіряється. Потрібне реальне обладнання або значення каналу (Q-02).")
        L.check("Ліміт гостя діє під навантаженням", bool(sp["g"]) and max(sp["g"]) <= cfg.GUEST_MBIT * 1.1, f"<= {cfg.GUEST_MBIT} Мбіт/с")
    finally:
        stop.set()
        th.join(timeout=20)
        ssh("sh /root/load_clients.sh down", 300)
        for z in (0, 1, 2):
            for s in sessions(z):
                if s["macAddress"].startswith("02:67:01"):
                    opn.api(f"/api/captiveportal/session/disconnect/{z}", data={"sessionId": s["sessionId"]})
        drop_load_accounts()
        L.add("\nПісля тесту: клієнтів навантаження, їхні сесії, акаунти й ваучери видалено.")
    with open(os.path.join(OUT, "TC-02-load.json"), "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False)
    chart(samples)
    L.add("Графік навантаження: TC-02-load.png (сесії за сегментами й трафік на інтерфейсах, дані — TC-02-load.json)")
    return L.finish()


def chart(samples):
    if not samples:
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    t0 = samples[0]["t"]
    x = [(s["t"] - t0) / 60 for s in samples]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8, 5.2), sharex=True)
    for seg, c in (("STUDENTS", "#2b6cb0"), ("TEACHERS", "#2f855a"), ("GUESTS", "#c05621")):
        a1.plot(x, [s.get(seg, 0) for s in samples], label=seg, color=c)
    a1.set_ylabel("Активні сесії")
    a1.legend(loc="upper left", fontsize=8)
    a1.grid(alpha=.3)
    for k, name, c in (("opt1", "STUDENTS", "#2b6cb0"), ("opt2", "TEACHERS", "#2f855a"), ("opt3", "GUESTS", "#c05621"), ("wan", "WAN", "#555")):
        a2.plot(x, [s.get(k, 0) for s in samples], label=name, color=c)
    a2.set_ylabel("Трафік, Мбіт/с")
    a2.set_xlabel("Час від початку тесту, хв")
    a2.legend(loc="upper left", fontsize=8)
    a2.grid(alpha=.3)
    fig.suptitle("TC-02: змодельоване пікове навантаження (дані панелі моніторингу)", fontsize=11)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "TC-02-load.png"), dpi=130)


# ======================================================================= TC-10
def tc10():
    L = Log("TC-10", "Відтворення конфігурації за інструкцією (R-11), модельна перевірка")
    L.add("Перевірка: налаштування мережі заново застосовуються скриптами з інструкції (README.md, розділ «Як відтворити»),"
          " після чого всі тести проходять. Перевірку незалежною людиною передано на етап Testing.")
    py = sys.executable
    for script in ("opnsense_setup_sprint1.py", "opnsense_setup_sprint2.py"):
        r = subprocess.run([py, os.path.join(s1.BASE, script)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=s1.BASE, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=600)
        fails = [line for line in r.stdout.splitlines() if "FAIL" in line]
        L.add(f"\n$ python {script}  (код {r.returncode})")
        L.add("    " + "\n    ".join(r.stdout.strip().splitlines()))
        L.check(f"{script} виконано без помилок", r.returncode == 0 and not fails, "усі кроки OK")
    time.sleep(5)
    L.add("\n-- Регрес Sprint 1 після повторного розгортання")
    res = [s1.tc04(), s1.tc03(), s1.tc08()]
    s1.OUT = OUT
    for r in res:
        n = sum(c["ok"] for c in r["checks"])
        L.add(f"{r['tc']}: {r['status']} ({n}/{len(r['checks'])})")
        L.check(f"{r['tc']} після повторного розгортання", r["status"] == "PASS", "PASS")
    return L.finish()


TESTS = {"TC-05": tc05, "TC-07": tc07, "TC-02": tc02, "TC-10": tc10}

if __name__ == "__main__":
    want = sys.argv[1:] or list(TESTS)
    s1.start_listeners()
    results = [TESTS[t]() for t in want]
    path = os.path.join(OUT, "results.json")
    old = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    merged = {r["tc"]: r for r in old}
    merged.update({r["tc"]: r for r in results})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(list(merged.values()), f, ensure_ascii=False, indent=2)
    print("\nЗведення Sprint 2 (OPNsense):")
    for r in results:
        n = sum(c["ok"] for c in r["checks"])
        print(f"  {r['tc']}: {r['status']}  ({n}/{len(r['checks'])})  {r['title']}")
        for c in r["checks"]:
            if not c["ok"]:
                print(f"       FAIL: {c['check']}  [очікувалось: {c['expected']}]")
