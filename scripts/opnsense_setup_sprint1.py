"""Sprint 1: налаштування OPNsense через API — DHCP, аліаси та правила firewall для сегментів.
Скрипт ідемпотентний: усі об'єкти мають префікс '67space', перед створенням старі видаляються.

Передумова: VLAN 10/20/30 створено й призначено інтерфейсам opt1 (STUDENTS), opt2 (TEACHERS),
opt3 (GUESTS) з адресами 10.67.<10|20|30>.1/24.
"""
import opn

TAG = "67space"
ALIAS = "v67"   # ім'я аліаса не може починатися з цифри
SEGMENTS = {  # інтерфейс -> (назва, початок діапазону, кінець, оренда в секундах)
    "opt1": ("STUDENTS", "10.67.10.100", "10.67.10.199", 28800),
    "opt2": ("TEACHERS", "10.67.20.100", "10.67.20.199", 28800),
    "opt3": ("GUESTS",   "10.67.30.100", "10.67.30.199", 3600),
}


def ok(r, what):
    good = r.get("result") in ("saved", "deleted") or str(r.get("status", "")).strip().lower() in ("ok", "done")
    print(("  OK   " if good else "  FAIL ") + what, "" if good else str(r)[:300])
    return good


def clean(search, delete, key="description"):
    for row in opn.api(search).get("rows", []):
        if TAG in str(row.get(key, "")) or str(row.get("name", "")).startswith(ALIAS + "_"):
            opn.api(f"{delete}/{row['uuid']}", data={})


# ---------------------------------------------------------------- DHCP
def dhcp():
    print("DHCP (dnsmasq)")
    clean("/api/dnsmasq/settings/searchRange", "/api/dnsmasq/settings/delRange")
    cur = opn.api("/api/dnsmasq/settings/get")["dnsmasq"]["interface"]
    want = ["lan"] + list(SEGMENTS)
    ok(opn.api("/api/dnsmasq/settings/set", data={"dnsmasq": {"interface": ",".join(want)}}), "dnsmasq слухає " + ",".join(want))
    for ifc, (name, start, end, lease) in SEGMENTS.items():
        ok(opn.api("/api/dnsmasq/settings/addRange", data={"range": {
            "interface": ifc, "start_addr": start, "end_addr": end, "lease_time": str(lease),
            "description": f"{TAG} {name}"}}), f"діапазон {name}: {start}-{end}, оренда {lease} с")
    ok(opn.api("/api/dnsmasq/service/reconfigure", data={}), "dnsmasq застосовано")


# ---------------------------------------------------------------- Aliases
def aliases():
    print("Аліаси")
    clean("/api/firewall/alias/searchItem", "/api/firewall/alias/delItem", key="name")
    for name, content, descr in [
        (f"{ALIAS}_PRIVATE_NETS", "10.0.0.0/8\n172.16.0.0/12\n192.168.0.0/16", "Усі приватні мережі (ізоляція від внутрішніх сегментів)"),
        (f"{ALIAS}_UNI_INTERNAL", "10.67.50.0/24\n192.168.56.20", "Внутрішні ресурси університету (змодельовані: сервер 192.168.56.20; TBD Q-02, уточнити список)"),
    ]:
        ok(opn.api("/api/firewall/alias/addItem", data={"alias": {
            "enabled": "1", "name": name, "type": "network", "content": content, "description": descr}}), f"аліас {name}")
    ok(opn.api("/api/firewall/alias/reconfigure", data={}), "аліаси застосовано")


# ---------------------------------------------------------------- Firewall
def rule(ifc, action, descr, proto="any", dst="any", dport="", sport="", log=False):
    return {"rule": {
        "enabled": "1", "action": action, "quick": "1", "interface": ifc, "direction": "in",
        "ipprotocol": "inet", "protocol": proto, "source_net": "any", "source_port": sport,
        "destination_net": dst, "destination_port": dport, "log": "1" if log else "0",
        "description": f"{TAG} {descr}"}}


def firewall():
    print("Firewall")
    clean("/api/firewall/filter/searchRule", "/api/firewall/filter/delRule")
    priv, uni = f"{ALIAS}_PRIVATE_NETS", f"{ALIAS}_UNI_INTERNAL"
    for ifc, (name, *_r) in SEGMENTS.items():
        rules = [
            rule(ifc, "pass", f"{name}: DHCP", proto="UDP", dst="any", dport="67", sport="68"),
            rule(ifc, "pass", f"{name}: DNS на шлюзі", proto="TCP/UDP", dst=f"{ifc}ip", dport="53"),
        ]
        if name != "GUESTS":   # студенти й викладачі мають внутрішні ресурси (R-03, R-08)
            rules.append(rule(ifc, "pass", f"{name}: внутрішні ресурси університету", dst=uni))
        rules += [
            rule(ifc, "block", f"{name}: заборона до інших сегментів і керування (R-03, R-08)", dst=priv, log=True),
            rule(ifc, "pass", f"{name}: інтернет"),
        ]
        for r in rules:
            ok(opn.api("/api/firewall/filter/addRule", data=r), r["rule"]["description"])
    ok(opn.api("/api/firewall/filter/apply", data={}), "правила застосовано")


def clean_rules():
    clean("/api/firewall/filter/searchRule", "/api/firewall/filter/delRule")


if __name__ == "__main__":
    dhcp()
    clean_rules()   # правила посилаються на аліаси, тому видаляємо їх першими
    aliases()
    firewall()
