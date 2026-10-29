"""Заняття 17 (Troubleshooting + Retest): retest виправлених / діагностованих issues і regression check.

ISS-01 (D-04)  fix monitor.py  -> retest NT-04, regression TC-05 (моніторинг у штатному режимі)
ISS-02 (D-03)  діагностика (diag_voucher_overlap.py) -> retest NT-01, sanity TC-04 (вхід за ваучером / портал)
ISS-03 (D-01)  діагностика -> TC-02 x3 із журналом кожного запиту, розбір відмов за сегментами
ISS-04 (новий, NT-04 A)  fix: тест ping точок 2 x 2 с -> NT-04 x3, regression TC-05, 5 хв без хибних тривог
Перевірка вмісту конфігурації OPNsense до й після (= freeze-v1.0). Логи: ../evidence/retest17/.

Запуск:  python scripts/run_retest17.py [ISS-01 ISS-02 ISS-03 ISS-04]   (до зміни Monit, база freeze-v1.0)
         python scripts/run_retest17.py FINAL                         (фінальний прогін на stable-v1.1)
"""
import collections
import json
import os
import sys
import time

import run_opn_tests as s1
import run_testing1 as t1
import run_negative_tests as nt
import monitor

OUT = os.path.join(s1.BASE, "..", "evidence", "retest17")
os.makedirs(OUT, exist_ok=True)
s1.OUT = t1.s2.OUT = nt.OUT = OUT
TC02_RUNS = 3


def keep(name, suffix, files=()):
    """Лог тесту з тією самою назвою в кількох прогонах — зберегти під окремим іменем."""
    for f in (f"{name}.log",) + tuple(files):
        src = os.path.join(OUT, f)
        if os.path.exists(src):
            base, ext = os.path.splitext(f)
            os.replace(src, os.path.join(OUT, f"{base}-{suffix}{ext}"))


def iss01():
    r = [nt.nt04(), t1.s2.tc05()]
    return r


def iss02():
    return [nt.nt01(), s1.tc04()]


def iss03():
    res, lines = [], []
    for i in range(1, TC02_RUNS + 1):
        r = t1.s2.tc02()
        r["tc"] = f"TC-02-run{i}"
        rows = [x.split() for x in open(os.path.join(OUT, "TC-02-requests.txt"), encoding="utf-8").read().splitlines() if x]
        bad = [x for x in rows if x[2] not in ("200", "301")]
        by_seg = collections.Counter({"s": "STUDENTS", "t": "TEACHERS", "g": "GUESTS"}[x[0][3]] for x in bad)
        by_kind = collections.Counter(x[1] for x in bad)
        lines.append(f"прогін {i}: {r['status']}, запитів {len(rows)}, відмов {len(bad)}; за сегментами {dict(by_seg)}; "
                     f"за типом {dict(by_kind)}; приклади {bad[:4]}")
        print(lines[-1])
        keep("TC-02", f"run{i}", ("TC-02-requests.txt", "TC-02-load.json", "TC-02-load.png"))
        res.append(r)
    open(os.path.join(OUT, "ISS-03-tc02-analysis.log"), "w", encoding="utf-8").write(
        f"=== ISS-03 (D-01): TC-02 x{TC02_RUNS}, розбір відмов, {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n" + "\n".join(lines) + "\n")
    return res


def stability(minutes=5):
    """ISS-04: коротший тест ping не дає хибних тривог — усі точки працюють, нових подій DOWN немає."""
    L = s1.Log("ISS-04-stability", f"Хибні тривоги Monit: {minutes} хв при справних точках доступу")
    before = len(monitor.read(monitor.EVENTS))
    srv = monitor.serve(8067)
    downs, polls = 0, 0
    try:
        t0 = time.time()
        while time.time() - t0 < minutes * 60:
            st = json.loads(t1.s2.get("http://127.0.0.1:8067/status.json"))
            polls += 1
            downs += sum(not a["up"] for a in st["aps"].values())
            time.sleep(10)
    finally:
        srv.shutdown()
    new = monitor.read(monitor.EVENTS)[before:]
    L.add(f"[панель] опитувань {polls}, показів «НЕДОСТУПНА» {downs}; нових подій у events.csv: {len(new)} {new[:3]}")
    L.check("Жодного хибного «НЕДОСТУПНА» при справних точках", downs == 0, "0")
    L.check("Жодної нової події DOWN у журналі", not any(e["event"] == "DOWN" for e in new), "0 DOWN")
    return L.finish()


def iss04():
    res = []
    for i in range(1, 4):
        r = nt.nt04()
        r["tc"] = f"NT-04-fix-run{i}"
        keep("NT-04", f"fix-run{i}", ("dashboard-all-ap-down.html", "dashboard-api-lost.html"))
        res.append(r)
    keep("TC-05", "before-fix")   # TC-05 з кроку ISS-01 (до зміни Monit) лишається окремим логом
    r = t1.s2.tc05()
    r["tc"] = "TC-05-after-ISS-04"
    keep("TC-05", "after-ISS-04", ("dashboard-normal.html", "dashboard-ap2-down.html"))
    return res + [r, stability()]


def final():
    """Фінальний прогін на stable-v1.1 в одному процесі: виправлення ISS-01 / ISS-04 + ключові сценарії."""
    res = []
    for name, fn in (("NT-04", nt.nt04), ("TC-05", t1.s2.tc05), ("TC-04", s1.tc04), ("TC-08", s1.tc08)):
        r = fn()
        r["tc"] = f"{name}-final"
        keep(name, "final", ("dashboard-all-ap-down.html", "dashboard-api-lost.html", "dashboard-normal.html", "dashboard-ap2-down.html"))
        res.append(r)
    return res


STEPS = {"ISS-01": iss01, "ISS-02": iss02, "ISS-03": iss03, "ISS-04": iss04, "FINAL": final}

if __name__ == "__main__":
    want = sys.argv[1:] or list(STEPS)
    t0 = time.time()
    ver = "stable-v1.1" if want == ["FINAL"] else "freeze-v1.0"   # ISS-04 змінив конфігурацію -> stable-v1.1
    sfx = "-final" if ver != "freeze-v1.0" else ""
    results = [t1.baseline("before" + sfx, ver)]
    s1.start_listeners()
    for k in want:
        for r in STEPS[k]():
            r["issue"] = k
            results.append(r)
    results.append(t1.baseline("after" + sfx, ver))
    path = os.path.join(OUT, "results.json")
    old = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    merged = {(r.get("issue"), r["tc"]): r for r in old}
    merged.update({(r.get("issue"), r["tc"]): r for r in results})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(list(merged.values()), f, ensure_ascii=False, indent=2)
    print(f"\nRetest заняття 17, {round((time.time() - t0) / 60, 1)} хв:")
    for r in results:
        n = sum(c["ok"] for c in r["checks"])
        print(f"  {r.get('issue', ''):7} {r['tc']}: {r['status']}  ({n}/{len(r['checks'])})  {r['title']}")
        for c in r["checks"]:
            if not c["ok"]:
                print(f"       FAIL: {c['check']}  [очікувалось: {c['expected']}]")
