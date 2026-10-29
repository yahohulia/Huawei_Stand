"""NT-01, повтор кроку 2 (передача ваучера другому пристрою) 6 разів: скільки сесій лишається у ваучера."""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import opn, run_opn_tests as s1
ssh = s1.ssh
ns = "dev-guest2"
ssh(f"ip netns add {ns}; ip link add link eth0 name mv-{ns} address 02:67:00:00:30:02 type macvlan mode bridge; "
    f"ip link set mv-{ns} netns {ns}; mkdir -p /etc/netns/{ns}; : > /etc/netns/{ns}/resolv.conf; "
    f"ip netns exec {ns} sh -c 'ip link set lo up; ip link set mv-{ns} name lan0; ip link set lan0 up; "
    f"ip link add link lan0 name v30 type vlan id 30; ip link set v30 up; "
    f"udhcpc -i v30 -n -q -t 5 -T 2 -s /usr/share/udhcpc/default.script >/dev/null 2>&1'")
out = []
try:
    for i in range(1, 7):
        s1.reset_sessions()
        v = opn.api("/api/captiveportal/voucher/generateVouchers/Vouchers",
                    data={"count": "1", "validity": "600", "expirytime": "0", "vouchergroup": "67space-nt"})[0]
        st = []
        for d in ("dev-guest", ns):
            _, r = ssh(f"ip netns exec {d} curl -s -m 8 -d 'user={v['username']}&password={v['password']}' "
                       f"http://10.67.30.1:8002/api/captiveportal/access/logon/2/")
            st.append(json.loads(r).get("clientState"))
        time.sleep(3)
        own = [s for s in (opn.api("/api/captiveportal/session/list/2") or []) if s["userName"] == v["username"]]
        net = [ssh(f"ip netns exec {d} curl -s -m 6 -o /dev/null -w '%{{http_code}}' http://1.1.1.1/")[1] for d in ("dev-guest", ns)]
        line = (f"спроба {i}: ваучер {v['username']}, входи {st}, сесій ваучера в OPNsense {len(own)} "
                f"({', '.join(s['macAddress'] for s in own)}), HTTP dev-guest/dev-guest2: {net}")
        print(line); out.append(line)
finally:
    ssh(f"for p in $(ip netns pids {ns}); do kill $p; done; ip netns del {ns}; rm -rf /etc/netns/{ns}")
    opn.api("/api/captiveportal/voucher/dropVoucherGroup/Vouchers/67space-nt", data={})
    s1.reset_sessions()
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "NT-01-share-repro.log"), "w", encoding="utf-8").write(
    f"=== NT-01, повтор кроку 2: один ваучер на двох пристроях (6 спроб), {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n"
    "Налаштування зони GUESTS: concurrentlogins = 0 (одночасні входи одного акаунта заборонені)\n" + "\n".join(out) + "\n")
