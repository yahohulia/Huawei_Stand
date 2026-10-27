"""Sprint 2: налаштування OPNsense через API — ліміти гостьових сесій, обмеження швидкості гостей,
моніторинг точок доступу (Monit) і строк зберігання журналів.
Ідемпотентно: об'єкти з префіксом '67space' перед створенням видаляються.

Числові значення — припущення моделі (у вимогах TBD, Q-02 / OQ-6). Швидкість гостей зменшено під
вихід стенда в інтернет (~10–15 Мбіт/с через NAT VirtualBox), щоб ліміт можна було виміряти.
"""
import opn

TAG = "67space"
GUEST_IDLE_MIN = 15        # R-07: гостя без активності відключає через 15 хв
GUEST_HARD_MIN = 60        # R-07: сесія гостя не довша за 60 хв, далі — повторний вхід
GUEST_MBIT = 3             # R-07: ліміт на кожен гостьовий пристрій (не нижче порогу R-06 моделі = 2 Мбіт/с)
MONIT_INTERVAL_S = 10      # R-05: як часто Monit перевіряє точки доступу
LOG_DAYS = 31              # R-14: скільки днів зберігаються журнали (строк TBD, OQ-6)
APS = {"AP-1": "192.168.56.31", "AP-2": "192.168.56.32", "AP-3": "192.168.56.33"}


def ok(r, what):
    good = r.get("result") in ("saved", "deleted") or str(r.get("status", "")).strip().lower() in ("ok", "done")
    print(("  OK   " if good else "  FAIL ") + what, "" if good else str(r)[:300])
    return good


def selected(field):
    """Вибране значення поля-списку з відповіді get* OPNsense."""
    return [k for k, v in field.items() if isinstance(v, dict) and v.get("selected")]


# ---------------------------------------------------------------- R-07: тайм-аути гостьової зони
def guest_session():
    print("Гостьова зона captive portal")
    for row in opn.api("/api/captiveportal/settings/searchZones")["rows"]:
        if row["description"] == f"{TAG} GUESTS":
            ok(opn.api(f"/api/captiveportal/settings/setZone/{row['uuid']}", data={"zone": {
                "idletimeout": str(GUEST_IDLE_MIN), "hardtimeout": str(GUEST_HARD_MIN), "authservers": "Vouchers"}}),
               f"GUESTS: idle {GUEST_IDLE_MIN} хв, hard {GUEST_HARD_MIN} хв, вхід за ваучерами")
    ok(opn.api("/api/captiveportal/service/reconfigure", data={}), "captive portal застосовано")


# ---------------------------------------------------------------- R-07: обмеження швидкості гостей
def shaper():
    print(f"Traffic shaper: {GUEST_MBIT} Мбіт/с на кожен гостьовий пристрій")
    for row in opn.api("/api/trafficshaper/settings/searchRules").get("rows", []):
        if TAG in row.get("description", ""):
            opn.api(f"/api/trafficshaper/settings/delRule/{row['uuid']}", data={})
    for row in opn.api("/api/trafficshaper/settings/searchPipes").get("rows", []):
        if TAG in row.get("description", ""):
            opn.api(f"/api/trafficshaper/settings/delPipe/{row['uuid']}", data={})
    # маска по IP гостя: окрема "труба" на кожен пристрій, а не одна на весь сегмент
    for direction, mask, what in (("out", "dst-ip", "завантаження"), ("in", "src-ip", "вивантаження")):
        p = opn.api("/api/trafficshaper/settings/addPipe", data={"pipe": {
            "enabled": "1", "bandwidth": str(GUEST_MBIT), "bandwidthMetric": "Mbit", "mask": mask,
            "description": f"{TAG} GUESTS {what}"}})
        ok(p, f"pipe {what} {GUEST_MBIT} Мбіт/с, маска {mask}")
        ok(opn.api("/api/trafficshaper/settings/addRule", data={"rule": {
            "enabled": "1", "sequence": "10" if direction == "out" else "11", "interface": "opt3", "proto": "ip",
            "source": "any", "destination": "any", "direction": direction, "target": p.get("uuid", ""),
            "description": f"{TAG} GUESTS {what}"}}), f"правило shaper GUESTS ({direction})")
    ok(opn.api("/api/trafficshaper/service/reconfigure", data={}), "shaper застосовано")


# ---------------------------------------------------------------- R-05: стан точок доступу (Monit)
def monit():
    print("Monit: перевірка доступності точок доступу")
    m = opn.api("/api/monit/settings/get")["monit"]
    ping = next(u for u, t in m["test"].items() if t["name"] == "Ping")
    for u, s in m["service"].items():
        if TAG in s.get("description", ""):
            opn.api(f"/api/monit/settings/delService/{u}", data={})
    ok(opn.api("/api/monit/settings/set", data={"monit": {"general": {
        "enabled": "1", "interval": str(MONIT_INTERVAL_S), "startdelay": "0"}}}), f"Monit увімкнено, інтервал {MONIT_INTERVAL_S} с")
    for name, ip in APS.items():
        ok(opn.api("/api/monit/settings/addService", data={"service": {
            "enabled": "1", "name": name, "type": "host", "address": ip, "tests": ping,
            "description": f"{TAG} точка доступу {name}"}}), f"{name} ({ip}): ping")
    ok(opn.api("/api/monit/service/reconfigure", data={}), "Monit застосовано")


# ---------------------------------------------------------------- R-14: зберігання журналів
def logging():
    print("Журнали")
    ok(opn.api("/api/syslog/settings/set", data={"syslog": {"general": {"maxpreserve": str(LOG_DAYS)}}}),
       f"журнали зберігаються {LOG_DAYS} днів")
    ok(opn.api("/api/syslog/service/reconfigure", data={}), "syslog застосовано")


if __name__ == "__main__":
    guest_session()
    shaper()
    monit()
    logging()
