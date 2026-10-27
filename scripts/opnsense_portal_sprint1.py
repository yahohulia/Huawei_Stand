"""Sprint 1: captive portal на OPNsense — групи, тестові акаунти й по одній зоні на сегмент.

Кожна зона прив'язана до свого інтерфейсу й пускає лише членів своєї групи (authEnforceGroup),
тому студентський акаунт не увійде в гостьову зону й навпаки. Ідемпотентно (префікс v67-).
Усі паролі тестові, лише для лабораторної VM.
"""
import opn

GROUPS = {"v67-students": "67space: студенти", "v67-teachers": "67space: викладачі", "v67-guests": "67space: гості"}
USERS = [  # логін, пароль, група
    ("student01", "Stud01-lab", "v67-students"),
    ("student02", "Stud02-lab", "v67-students"),
    ("teacher01", "Teach01-lab", "v67-teachers"),
    ("guest01", "Guest01-lab", "v67-guests"),
]
ZONES = [  # інтерфейс, група, опис, сервер автентифікації, hard timeout (ХВИЛИНИ), 0 = без обмеження
    ("opt1", "v67-students", "67space STUDENTS", "Local Database", 0),
    ("opt2", "v67-teachers", "67space TEACHERS", "Local Database", 0),
    ("opt3", "v67-guests",   "67space GUESTS",   "Vouchers",       60),   # сервер Vouchers створюється вручну в GUI
]


def show(r, what):
    good = r.get("result") in ("saved", "deleted") or str(r.get("status", "")).lower() in ("ok", "done")
    print(("  OK   " if good else "  FAIL ") + what, "" if good else str(r)[:400])
    return good


def by_name(search, name):
    for row in opn.api(search).get("rows", []):
        if row.get("name") == name:
            return row
    return None


def accounts():
    print("Групи й акаунти")
    for u, _, _ in USERS:
        row = by_name("/api/auth/user/search", u)
        if row:
            opn.api(f"/api/auth/user/del/{row['uuid']}", data={})
    for g in GROUPS:
        row = by_name("/api/auth/group/search", g)
        if row:
            opn.api(f"/api/auth/group/del/{row['uuid']}", data={})
    for g, descr in GROUPS.items():
        show(opn.api("/api/auth/group/add", data={"group": {"name": g, "description": descr, "scope": "user"}}), f"група {g}")
    for name, pw, g in USERS:
        grp = by_name("/api/auth/group/search", g)
        show(opn.api("/api/auth/user/add", data={"user": {
            "name": name, "password": pw, "scope": "user", "descr": f"67space тестовий акаунт ({g})",
            "group_memberships": grp["gid"]}}), f"акаунт {name} -> {g}")


def zones():
    print("Captive portal")
    for row in opn.api("/api/captiveportal/settings/searchZones").get("rows", []):
        if "67space" in row.get("description", ""):
            opn.api(f"/api/captiveportal/settings/delZone/{row['uuid']}", data={})
    for ifc, g, descr, auth, hard in ZONES:
        grp = by_name("/api/auth/group/search", g)
        show(opn.api("/api/captiveportal/settings/addZone", data={"zone": {
            "enabled": "1", "interfaces": ifc, "authservers": auth,
            "authEnforceGroup": grp["gid"], "concurrentlogins": "0", "idletimeout": "0",
            "hardtimeout": str(hard), "description": descr}}), f"зона {descr} (інтерфейс {ifc}, група {g})")
    show(opn.api("/api/captiveportal/service/reconfigure", data={}), "captive portal застосовано")


if __name__ == "__main__":
    accounts()
    zones()
