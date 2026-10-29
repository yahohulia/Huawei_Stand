"""Testing 2 (заняття 16): negative / boundary scenarios на базовій версії freeze-v1.0.

NT-01  R-07 / TC-07  ваучер гостя: передача іншому пристрою та закінчення строку дії (межа validity)
NT-02  R-03, R-08 / TC-03, TC-08  підміна адреси в гостьовому VLAN (чужа підмережа, адреса не з DHCP)
NT-03  R-04 / TC-04  некоректне введення й підбір пароля на сторінці входу
NT-04  R-05 / TC-05  моніторинг при збої всіх точок доступу та втраті зв'язку з OPNsense

Конфігурацію OPNsense сценарії не змінюють (ваучери NT-01 — окрема група, після тесту видаляється);
SHA-256 конфігурації перевіряється до й після. Логи: ../evidence/testing2/.
Запуск:  python scripts/run_negative_tests.py [NT-01 NT-04 ...]
"""
import json
import os
import sys
import time

import opn
import monitor
import run_opn_tests as s1
import run_testing1 as t1

OUT = os.path.join(s1.BASE, "..", "evidence", "testing2")
os.makedirs(OUT, exist_ok=True)
s1.OUT = OUT
ssh, Log, DEV = s1.ssh, s1.Log, s1.DEV
INTERNET, INTRANET = s1.INTERNET, s1.INTRANET


def guest_sessions():
    r = opn.api("/api/captiveportal/session/list/2")
    return r if isinstance(r, list) else []


def http(L, dev, url, m=6):
    _, out = L.run(dev, f'curl -s -m {m} -o /dev/null -w "%{{http_code}}" {url}')
    return out


# ======================================================================= NT-01
def nt01():
    L = Log("NT-01", "Ваучер гостя: передача іншому пристрою і закінчення строку дії (R-07)")
    L.add("Ваучер на 120 с (окрема група 67space-nt); другий гостьовий пристрій dev-guest2 (MAC 02:67:00:00:30:02, VLAN 30).")
    s1.reset_sessions()
    opn.api("/api/captiveportal/voucher/dropVoucherGroup/Vouchers/67space-nt", data={})
    v = opn.api("/api/captiveportal/voucher/generateVouchers/Vouchers",
                data={"count": "1", "validity": "120", "expirytime": "0", "vouchergroup": "67space-nt"})[0]
    ns = "dev-guest2"
    ssh(f"ip netns add {ns}; ip link add link eth0 name mv-{ns} address 02:67:00:00:30:02 type macvlan mode bridge; "
        f"ip link set mv-{ns} netns {ns}; mkdir -p /etc/netns/{ns}; : > /etc/netns/{ns}/resolv.conf; "
        f"ip netns exec {ns} sh -c 'ip link set lo up; ip link set mv-{ns} name lan0; ip link set lan0 up; "
        f"ip link add link lan0 name v30 type vlan id 30; ip link set v30 up; "
        f"udhcpc -i v30 -n -q -t 5 -T 2 -s /usr/share/udhcpc/default.script >/dev/null 2>&1'; "
        f"echo nameserver 10.67.30.1 > /etc/netns/{ns}/resolv.conf")
    DEV[ns] = dict(DEV["dev-guest"])
    try:
        L.add(f"[{ns}] адреса {s1.ip_of(ns)}")
        L.add("\n-- Крок 1. Вхід за ваучером на першому пристрої")
        st = L.logon("dev-guest", user=v["username"], pw=v["password"])
        t_login = time.time()
        L.check("Гість увійшов за ваучером", st == "AUTHORIZED", "AUTHORIZED")
        L.add("\n-- Крок 2. Той самий ваучер на другому пристрої (ваучер передали іншій людині)")
        st2 = L.logon(ns, user=v["username"], pw=v["password"])
        time.sleep(2)
        own = [s for s in guest_sessions() if s["userName"] == v["username"]]
        for s in own:
            L.add(f"[OPNsense] сесія ваучера: {s['ipAddress']}  {s.get('macAddress')}")
        codes = {d: http(L, d, f"http://{INTERNET}/") for d in ("dev-guest", ns)}
        L.check("Один ваучер — не більше однієї активної сесії", len(own) <= 1, "<= 1 сесія")
        L.check("Інтернет має не більше одного пристрою з цим ваучером",
                sum(c in ("200", "301") for c in codes.values()) <= 1, "<= 1 пристрій")
        L.add("\n-- Крок 3. Строк дії ваучера (120 с) закінчується")
        ended = None
        while time.time() - t_login < 360:
            if not [s for s in guest_sessions() if s["userName"] == v["username"]]:
                ended = round(time.time() - t_login)
                break
            time.sleep(5)
        L.add(f"[OPNsense] сесію ваучера закрито через {ended} с після першого входу")
        L.check("Сесію закрито автоматично після закінчення строку ваучера", ended is not None and ended <= 240,
                "≈ 120 с (+ цикл перевірки OPNsense)")
        for d in ("dev-guest", ns):
            _, out = L.run(d, f'curl -s -m 6 -o /dev/null -w "%{{http_code}} %{{redirect_url}}" http://{INTERNET}/')
            L.check(f"{d}: після закінчення строку — знову сторінка входу", out.startswith("302"), "302 -> портал")
        L.add("\n-- Крок 4. Повторне використання простроченого ваучера")
        for d in ("dev-guest", ns):
            st = L.logon(d, user=v["username"], pw=v["password"])
            L.check(f"{d}: прострочений ваучер не пускає", st != "AUTHORIZED", "NOT_AUTHORIZED")
        L.check("Після спроб входу інтернет гостю закритий", http(L, "dev-guest", f"http://{INTERNET}/") == "302", "302")
        log = [r for r in t1.s2.portal_log(40) if v["username"] in r["line"]]
        for r in log[:6][::-1]:
            L.add(f"[OPNsense, журнал порталу] {r['timestamp']} {r['line'].strip()}")
    finally:
        DEV.pop(ns, None)
        ssh(f"for p in $(ip netns pids {ns}); do kill $p; done; ip netns del {ns}; rm -rf /etc/netns/{ns}")
        opn.api("/api/captiveportal/voucher/dropVoucherGroup/Vouchers/67space-nt", data={})
        L.add("\n[прибирання] dev-guest2 і група ваучерів 67space-nt видалені")
    return L.finish()


# ======================================================================= NT-02
def nt02():
    L = Log("NT-02", "Підміна адреси в гостьовому VLAN (R-03, R-08)")
    s1.reset_sessions()
    s1.start_listeners()
    L.logon("dev-student")
    t0 = (opn.api("/api/diagnostics/firewall/log/?limit=1") or [{"__timestamp__": "1970"}])[0]["__timestamp__"]
    try:
        L.add("\n-- Варіант A. Гість вручну ставить адресу зі студентської підмережі 10.67.10.200 (студент авторизований)")
        L.run("dev-guest", "ip addr flush dev v30; ip addr add 10.67.10.200/24 dev v30; ip route add 10.67.30.1 dev v30; "
                           "ip route add default via 10.67.30.1 dev v30; ip -4 -o addr show v30")
        L.check("Інтернет із чужою адресою недоступний", http(L, "dev-guest", f"http://{INTERNET}/") in ("000", "", "302"),
                "немає доступу")
        L.check("Внутрішній сервер недоступний", http(L, "dev-guest", f"http://{INTRANET}/") in ("000", ""), "немає з'єднання")
        st_ip = s1.ip_of("dev-student")
        L.check(f"Пристрій студента ({st_ip}) недоступний", http(L, "dev-guest", f"http://{st_ip}:8000/", 4) in ("000", ""),
                "немає з'єднання")
        L.check("Шлюз студентського VLAN 10.67.10.1 недоступний",
                http(L, "dev-guest", "-k https://10.67.10.1/", 4) in ("000", ""), "немає з'єднання")
        st = L.logon("dev-guest", user="student01", pw="Stud01-lab", zone=2, port=8002, gw="10.67.30.1")
        L.check("Акаунт студента з гостьового VLAN не входить", st != "AUTHORIZED", "NOT_AUTHORIZED")

        L.add("\n-- Варіант B. Адреса з гостьової підмережі, але не з DHCP (10.67.30.250), вхід не виконано")
        L.run("dev-guest", "ip addr flush dev v30; ip addr add 10.67.30.250/24 dev v30; "
                           "ip route add default via 10.67.30.1 dev v30; ip -4 -o addr show v30")
        _, out = L.run("dev-guest", f'curl -s -m 6 -o /dev/null -w "%{{http_code}} %{{redirect_url}}" http://{INTERNET}/')
        L.check("Без входу — перенаправлення на сторінку входу", out.startswith("302"), "302 -> портал")
        L.check("HTTPS в інтернет закритий", http(L, "dev-guest", f"-k https://{INTERNET}/", 5) in ("000", ""), "немає з'єднання")
        _, out = L.run("dev-guest", f'curl -s -m 6 -w " %{{http_code}} %{{redirect_url}}" http://{INTRANET}/')
        L.check("Внутрішній сервер недоступний (вміст сервера не отримано)", "INTRANET-OK" not in out
                and (out.split()[-1] in ("000",) or ":8002/" in out), "немає з'єднання або перенаправлення на портал")
        time.sleep(2)
        blocks = [r for r in s1.fw_blocks(t0, "10.67.") if r["src"] in ("10.67.10.200", "10.67.30.250")]
        L.add(f"\n[firewall log] block від підмінених адрес: {len(blocks)} записів")
        for r in blocks[:8]:
            L.add(f"    {r['__timestamp__']}  {r['interface']}  {r['src']} -> {r['dst']}  proto={r['protoname']} dport={r.get('dstport', '-')}")
        L.check("Firewall фіксує блокування трафіку з підмінених адрес", len(blocks) > 0, "є записи block")
    finally:
        L.add("\n-- Відновлення: адреса гостя знову по DHCP")
        L.run("dev-guest", "ip addr flush dev v30; ip route flush dev v30; "
                           "udhcpc -i v30 -n -q -t 5 -T 2 -s /usr/share/udhcpc/default.script >/dev/null 2>&1; "
                           "ip -4 -o addr show v30")
        ssh("echo nameserver 10.67.30.1 > /etc/netns/dev-guest/resolv.conf")
    L.check("Після відновлення гість отримав адресу DHCP і входить за ваучером",
            s1.ip_of("dev-guest").startswith("10.67.30.") and L.logon("dev-guest") == "AUTHORIZED", "AUTHORIZED")
    return L.finish()


# ======================================================================= NT-03
def nt03():
    L = Log("NT-03", "Некоректне введення й підбір пароля на сторінці входу (R-04)")
    s1.reset_sessions()
    url = "http://10.67.10.1:8000/api/captiveportal/access/logon/0/"

    def attempt(user, pw, show=True):
        rc, out = ssh(f"ip netns exec dev-student curl -s -m 8 -w '\\n%{{http_code}}' "
                      f"--data-urlencode {json.dumps('user=' + user)} --data-urlencode {json.dumps('password=' + pw)} {url}")
        body, _, code = out.rpartition("\n")
        try:
            state = json.loads(body).get("clientState", "?")
        except ValueError:
            state = "no-json"
        if show:
            L.add(f"[dev-student] вхід user={user[:40]!r}{'…(' + str(len(user)) + ')' if len(user) > 40 else ''} "
                  f"password={pw[:20]!r} -> HTTP {code}, clientState={state}")
        return code, state

    L.add("\n-- Крок 1. Некоректні й шкідливі значення полів")
    cases = [("", ""), ("student01", ""), ("", "Stud01-lab"), ("a" * 4096, "x"), ("' OR '1'='1", "' OR '1'='1"),
             ("student01' -- ", "x"), ("student01\" or \"1\"=\"1", "x"), ("<script>alert(1)</script>", "x"),
             ("../../etc/passwd", "x"), ("STUDENT01", "Stud01-lab"), ("student01", "stud01-lab"), ("student01 ", "Stud01-lab")]
    for u, p in cases:
        code, st = attempt(u, p)
        L.check(f"Відмова без збою: user={u[:24]!r}", st == "NOT_AUTHORIZED" and code == "200" or code in ("400", "413"),
                "NOT_AUTHORIZED або HTTP 400/413")
    L.add("\n-- Крок 2. Підбір пароля: 30 хибних паролів поспіль для student01")
    t0 = time.time()
    states = [attempt("student01", f"guess-{i:02d}", show=False)[1] for i in range(30)]
    L.add(f"[dev-student] 30 спроб за {round(time.time() - t0, 1)} с: "
          + ", ".join(f"{s} x{states.count(s)}" for s in sorted(set(states))))
    L.check("Жодна з 30 хибних спроб не дала доступу", all(s == "NOT_AUTHORIZED" for s in states), "30 x NOT_AUTHORIZED")
    L.add("\n-- Крок 3. Після атак: портал працює, доступ без входу закритий")
    _, out = L.run("dev-student", 'curl -s -m 6 -o /dev/null -w "%{http_code}" http://10.67.10.1:8000/index.html')
    L.check("Сторінка входу відкривається", out == "200", "200")
    _, out = L.run("dev-student", f'curl -s -m 6 -o /dev/null -w "%{{http_code}}" http://{INTERNET}/')
    L.check("Інтернет без входу закритий", out == "302", "302 -> портал")
    code, st = attempt("student01", "Stud01-lab")
    L.check("Правильний вхід після атак працює (немає відмови в обслуговуванні)", st == "AUTHORIZED", "AUTHORIZED")
    L.check("Після входу є інтернет", http(L, "dev-student", f"http://{INTERNET}/", 8) in ("200", "301"), "200/301")
    fails = [r for r in t1.s2.portal_log(80) if r["line"].strip().startswith("DENY student01")]
    L.add(f"\n[OPNsense, журнал порталу] невдалих входів student01 у журналі: {len(fails)}")
    for r in fails[:3]:
        L.add(f"    {r['timestamp']} {r['line'].strip()}")
    L.check("Невдалі спроби входу видно в журналі порталу", len(fails) >= 30, ">= 30 записів")
    return L.finish()


# ======================================================================= NT-04
def nt04():
    L = Log("NT-04", "Моніторинг: збій усіх точок доступу і втрата зв'язку з OPNsense (R-05)")
    s1.reset_sessions()
    for dev in DEV:
        L.logon(dev)
    srv = monitor.serve(8067)
    get = t1.s2.get
    try:
        L.add("\n-- Варіант A. Одночасно вимкнено AP-1, AP-2, AP-3")
        ssh("for a in ap-1 ap-2 ap-3; do ip netns exec $a ip link set mgmt0 down; done")
        t0, det = time.time(), None
        while time.time() - t0 < 120:
            st = json.loads(get("http://127.0.0.1:8067/status.json"))
            if len(st["aps"]) == 3 and not any(a["up"] for a in st["aps"].values()):
                det = round(time.time() - t0)
                break
            time.sleep(2)
        L.add(f"[панель] {', '.join(f'{n}: ' + ('UP' if a['up'] else 'DOWN') for n, a in sorted(st['aps'].items()))}; через {det} с")
        L.check("Усі 3 точки показано НЕДОСТУПНА не пізніше 60 с", det is not None and det <= 60, "3 x DOWN, <= 60 с")
        seg = {k: len(v) for k, v in st["segments"].items()}
        L.add(f"[панель] сесії під час збою: {seg}")
        L.check("Решта показників панелі працює під час збою", seg == {"STUDENTS": 1, "TEACHERS": 1, "GUESTS": 1}, "1/1/1")
        open(os.path.join(OUT, "dashboard-all-ap-down.html"), "w", encoding="utf-8").write(get("http://127.0.0.1:8067/"))
        ssh("for a in ap-1 ap-2 ap-3; do ip netns exec $a ip link set mgmt0 up; done")
        t0, back = time.time(), None
        while time.time() - t0 < 120:
            st = json.loads(get("http://127.0.0.1:8067/status.json"))
            if len(st["aps"]) == 3 and all(a["up"] for a in st["aps"].values()):
                back = round(time.time() - t0)
                break
            time.sleep(2)
        L.add(f"[панель] усі точки знову доступні через {back} с")
        L.check("Відновлення всіх точок видно не пізніше 60 с", back is not None and back <= 60, "<= 60 с")
        ev = monitor.read(monitor.EVENTS)[-6:]
        for e in ev:
            L.add(f"[events.csv] {e['time']}  {e['object']}  {e['event']}")
        L.check("Журнал подій: DOWN і UP для кожної з 3 точок",
                all(sum(e["object"] == n and e["event"] == x for e in ev) == 1 for n in ("AP-1", "AP-2", "AP-3") for x in ("DOWN", "UP")),
                "3 x DOWN + 3 x UP")

        L.add("\n-- Варіант B. Панель втрачає зв'язок з OPNsense (адреса API недоступна)")
        st = json.loads(get("http://127.0.0.1:8067/status.json"))
        L.add(f"[панель до збою] оновлено {st['updated']}; точки: "
              + ", ".join(f"{n} {'UP' if a['up'] else 'DOWN'}" for n, a in sorted(st["aps"].items())))
        host = opn.HOST
        opn.HOST = "192.168.56.99"
        t_cut = time.time()
        L.add(f"[{time.strftime('%H:%M:%S')}] панель звертається до OPNsense за недоступною адресою {opn.HOST}")
        try:
            st, t0 = None, time.time()
            while time.time() - t0 < 240:
                cur = json.loads(get("http://127.0.0.1:8067/status.json"))
                if cur["errors"] and cur.get("polled_epoch", cur.get("updated_epoch", 0)) > t_cut:
                    st = cur
                    break
                time.sleep(3)
            page = get("http://127.0.0.1:8067/")
            open(os.path.join(OUT, "dashboard-api-lost.html"), "w", encoding="utf-8").write(page)
        finally:
            opn.HOST = host
        L.check("Панель не впала й відповідає під час втрати зв'язку", bool(page), "сторінка відкривається")
        if st:
            L.add(f"[панель під час збою] оновлено {st['updated']}; помилки: {[x[:70] for x in st['errors']]}")
            L.add("[панель під час збою] точки: " + ", ".join(f"{n} {'UP' if a['up'] else 'DOWN'}" for n, a in sorted(st["aps"].items()))
                  + f"; сесії: { {k: len(v) for k, v in st['segments'].items()} }")
        L.check("Панель показує помилку джерела даних", bool(st and st["errors"]) and "Помилка джерела" in page, "повідомлення про помилку")
        stale = bool(st) and any(a["up"] for a in st["aps"].values())
        L.check("Точки доступу не показано «доступна», коли стан невідомий", st is not None and not stale,
                "стан «невідомо» замість останнього відомого")
        L.check("Час «Оновлено» не видає старі дані за свіжі", st is not None and (st.get("updated_epoch") or 0) < t_cut,
                "час останніх успішних даних (до обриву)")
        t0, ok = time.time(), False
        while time.time() - t0 < 60:
            cur = json.loads(get("http://127.0.0.1:8067/status.json"))
            if not cur["errors"]:
                ok = True
                break
            time.sleep(3)
        L.add(f"[панель] після відновлення зв'язку помилок немає: {ok}")
        L.check("Після відновлення зв'язку панель повертається до нормальної роботи", ok, "без помилок")
    finally:
        srv.shutdown()
        ssh("for a in ap-1 ap-2 ap-3; do ip netns exec $a ip link set mgmt0 up; done")
    return L.finish()


TESTS = {"NT-01": nt01, "NT-02": nt02, "NT-03": nt03, "NT-04": nt04}

if __name__ == "__main__":
    want = sys.argv[1:] or list(TESTS)
    t1.OUT = OUT
    t0 = time.time()
    results = [t1.baseline("before")]
    results += [TESTS[t]() for t in want]
    results.append(t1.baseline("after"))
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nTesting 2 / negative (freeze-v1.0), {round((time.time() - t0) / 60, 1)} хв:")
    for r in results:
        n = sum(c["ok"] for c in r["checks"])
        print(f"  {r['tc']}: {r['status']}  ({n}/{len(r['checks'])})  {r['title']}")
        for c in r["checks"]:
            if not c["ok"]:
                print(f"       FAIL: {c['check']}  [очікувалось: {c['expected']}]")
