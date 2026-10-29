"""Testing 1 (заняття 15): прогін пріоритетних Test ID із Test Strategy на базовій версії freeze-v1.0.

Перед тестами перевіряється, що стенд — саме freeze-v1.0 (SHA-256 конфігурації OPNsense = резервна копія Freeze),
після тестів — що конфігурація повернулась до базової (TC-07 за своєю процедурою тимчасово скорочує тайм-аут).
Тести ті самі, що в Sprint 1–2 (run_opn_tests.py, run_sprint2_tests.py); логи пишуться в ../evidence/testing1/.

Запуск:  python scripts/run_testing1.py                 (TC-03 TC-04 TC-08 TC-05 TC-07 TC-02, ~15 хв)
         python scripts/run_testing1.py TC-04 TC-08     (вибрані)
"""
import hashlib
import json
import os
import re
import sys
import time

import opn
import run_opn_tests as s1
import run_sprint2_tests as s2
import smoke_check

OUT = os.path.join(s1.BASE, "..", "evidence", "testing1")
os.makedirs(OUT, exist_ok=True)
s1.OUT = s2.OUT = OUT
BACKUP_SHA = open(smoke_check.BACKUP + ".sha256", encoding="utf-8").read().split()[0]
TESTS = {"TC-03": s1.tc03, "TC-04": s1.tc04, "TC-08": s1.tc08,
         "TC-05": s2.tc05, "TC-07": s2.tc07, "TC-02": s2.tc02}
ORDER = ["TC-04", "TC-03", "TC-08", "TC-05", "TC-07", "TC-02"]


def content_sha(data):
    """SHA-256 вмісту конфігурації без міток часу збереження (persisted_at, <time> ревізії): їх OPNsense
    оновлює при кожному збереженні, навіть якщо значення повернули до попередніх (TC-07)."""
    data = re.sub(rb' persisted_at="[\d.]+"', b"", data)
    data = re.sub(rb"<time>[\d.]+</time>", b"", data)
    return hashlib.sha256(data).hexdigest()


def baseline(tag, version="freeze-v1.0"):
    """version — резервна копія secrets/baseline/config-<version>.xml (freeze-v1.0, stable-v1.1 …)."""
    backup = smoke_check.BACKUP.replace("freeze-v1.0", version)
    backup_sha = open(backup + ".sha256", encoding="utf-8").read().split()[0]
    L = s1.Log(f"BASELINE-{tag}", f"Перевірка базової версії {version} ({'до' if tag.startswith('before') else 'після'} тестів)")
    fw = opn.api("/api/core/firmware/status").get("product", {}).get("product_version", "?")
    sha, data = smoke_check.config_sha()
    base = content_sha(open(backup, "rb").read())
    L.add(f"[OPNsense] версія {fw}")
    L.add(f"[OPNsense] SHA-256 конфігурації: {sha}\n[{version}] SHA-256 резервної копії: {backup_sha}"
          + ("  (однакові)" if sha == backup_sha else "  (різні: перевіряється вміст без міток часу збереження)"))
    L.add(f"[OPNsense] SHA-256 вмісту без міток часу: {content_sha(data)}\n[{version}] SHA-256 вмісту без міток часу: {base}")
    L.check(f"Вміст конфігурації OPNsense = базова версія {version}", content_sha(data) == base, "однакові SHA-256 вмісту")
    return L.finish()


if __name__ == "__main__":
    want = sys.argv[1:] or ORDER
    t0 = time.time()
    results = [baseline("before")]
    s1.start_listeners()
    results += [TESTS[t]() for t in want]
    results.append(baseline("after"))
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nTesting 1 (freeze-v1.0), {round((time.time() - t0) / 60, 1)} хв:")
    for r in results:
        n = sum(c["ok"] for c in r["checks"])
        print(f"  {r['tc']}: {r['status']}  ({n}/{len(r['checks'])})  {r['title']}")
        for c in r["checks"]:
            if not c["ok"]:
                print(f"       FAIL: {c['check']}  [очікувалось: {c['expected']}]")
