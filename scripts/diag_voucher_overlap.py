"""Заняття 17, ISS-02 (D-03): діагностика «один ваучер — два пристрої».

Гіпотеза: OPNsense пускає кожен правильний вхід одразу (logonAction), а одночасні сесії одного користувача
(concurrentlogins = 0) прибирає фоновий процес cp-background-process.py — цикл ~5 с, лишається найновіша сесія.
Тоді дві сесії одного ваучера існують кілька секунд після другого входу, а не постійно.
Скрипт N разів: вхід dev-guest, вхід dev-guest2 тим самим ваучером, далі щосекунди фіксує, скільки сесій
ваучера в OPNsense і в кого є інтернет, доки не лишиться одна (або 60 с).
Запуск: python scripts/diag_voucher_overlap.py [N]      Лог: ../evidence/retest17/ISS-02-overlap.log
"""
import json
import os
import sys
import time

import opn
import run_opn_tests as s1

OUT = os.path.join(s1.BASE, "..", "evidence", "retest17")
os.makedirs(OUT, exist_ok=True)
ssh, NS = s1.ssh, "dev-guest2"


def make_guest2():
    ssh(f"ip netns add {NS}; ip link add link eth0 name mv-{NS} address 02:67:00:00:30:02 type macvlan mode bridge; "
        f"ip link set mv-{NS} netns {NS}; mkdir -p /etc/netns/{NS}; : > /etc/netns/{NS}/resolv.conf; "
        f"ip netns exec {NS} sh -c 'ip link set lo up; ip link set mv-{NS} name lan0; ip link set lan0 up; "
        f"ip link add link lan0 name v30 type vlan id 30; ip link set v30 up; "
        f"udhcpc -i v30 -n -q -t 5 -T 2 -s /usr/share/udhcpc/default.script >/dev/null 2>&1'")


def drop_guest2():
    ssh(f"for p in $(ip netns pids {NS}); do kill $p; done; ip netns del {NS}; rm -rf /etc/netns/{NS}")


def logon(dev, v):
    _, r = ssh(f"ip netns exec {dev} curl -s -m 8 -d 'user={v['username']}&password={v['password']}' "
               f"http://10.67.30.1:8002/api/captiveportal/access/logon/2/")
    try:
        return json.loads(r).get("clientState")
    except ValueError:
        return "no-json"


def own(v):
    r = opn.api("/api/captiveportal/session/list/2")
    return [s for s in (r if isinstance(r, list) else []) if s["userName"] == v["username"]]


def net():
    # обидва пристрої одним викликом ssh: HTTP-код запиту в інтернет
    _, out = ssh("for d in dev-guest dev-guest2; do ip netns exec $d curl -s -m 3 -o /dev/null -w '%{http_code} ' http://1.1.1.1/; done")
    return out.split()


def main(n):
    lines = [f"=== ISS-02 (D-03): тривалість двох сесій одного ваучера, {n} спроб, {time.strftime('%Y-%m-%d %H:%M:%S')} ===",
             "Зона GUESTS: concurrentlogins = 0; фонова очистка OPNsense — цикл ~5 с (cp-background-process.py)."]
    windows = []
    make_guest2()
    try:
        for i in range(1, n + 1):
            s1.reset_sessions()
            v = opn.api("/api/captiveportal/voucher/generateVouchers/Vouchers",
                        data={"count": "1", "validity": "600", "expirytime": "0", "vouchergroup": "67space-nt"})[0]
            st1 = logon("dev-guest", v)
            time.sleep(1)
            st2 = logon(NS, v)
            t0 = time.time()
            trace, left = [], None
            while time.time() - t0 < 60:
                ss, codes = own(v), net()
                trace.append(f"{round(time.time() - t0)}с:{len(ss)}с/{'+'.join(codes)}")
                if len(ss) <= 1 and sum(c in ("200", "301") for c in codes) <= 1:
                    left = round(time.time() - t0, 1)
                    break
                time.sleep(1)
            keep = [s["macAddress"] for s in own(v)]
            windows.append(left)
            lines.append(f"спроба {i}: входи {st1}/{st2}; дві сесії / два пристрої з інтернетом зникли через {left} с; "
                         f"лишилась сесія {keep}; хід: {' '.join(trace)}")
            print(lines[-1])
    finally:
        drop_guest2()
        opn.api("/api/captiveportal/voucher/dropVoucherGroup/Vouchers/67space-nt", data={})
        s1.reset_sessions()
    ok = [w for w in windows if w is not None]
    lines.append(f"\nПІДСУМОК: у {len(ok)} з {n} спроб одночасний доступ закінчився сам; максимум {max(ok) if ok else '-'} с, "
                 f"у середньому {round(sum(ok) / len(ok), 1) if ok else '-'} с; лишається сесія останнього входу.")
    open(os.path.join(OUT, "ISS-02-overlap.log"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(lines[-1])


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 10)
