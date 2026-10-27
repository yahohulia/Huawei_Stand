"""Sprint 2: сторінка входу 67space для captive portal (R-10, UX).

Стандартна сторінка OPNsense англійською й без назви коворкінгу. Скрипт бере стандартний шаблон з
OPNsense, замінює тексти на українські, додає назву 67space і підказку, як увійти, та стилі для цих
елементів (поля й кнопка на всю ширину форми), завантажує шаблон і вмикає його в усіх зонах порталу.
Логіку входу (JavaScript) не змінює. Ідемпотентно. Перевірка верстки: portal_screens.py.
"""
import base64
import io
import urllib.request
import zipfile

import opn

NAME = "67space"
TEXT = [  # (стандартний текст, текст 67space) у index.html
    ("<title>Captive Portal login</title>", "<title>67space — вхід у Wi-Fi</title>"),
    ('<h1><a href="/"><img src="images/OPNsense-logo.svg" class="maintenance-logo" alt="OPNsense"></a></h1>',
     '<div><p class="brand-title">67space</p><p class="brand-sub">Wi-Fi студентського коворкінгу</p></div>'),
    ("Please sign in", "Вхід у мережу"),
    ('placeholder="Username"', 'placeholder="Логін або код ваучера"'),
    ('placeholder="Password"', 'placeholder="Пароль"'),
    ('id="signin" type="button">Sign in', 'id="signin" type="button">Увійти'),
    ('id="signin_anon" type="button">Sign in', 'id="signin_anon" type="button">Увійти'),
    ('id="logoff" type="button">Logout', 'id="logoff" type="button">Вийти з мережі'),
    ('"authentication failed"', '"Невірний логін, пароль або код ваучера"'),
    ('"login failed"', '"Не вдалося увійти"'),
    ('"unable to connect to authentication server"', '"Немає зв\'язку із сервером авторизації"'),
]
FOOTER_OLD = '<a class="deciso-brand"'
FOOTER = ('<p class="help">Студенти й викладачі — власний логін і пароль; гості — код ваучера на 1 годину '
          '(видає адміністратор коворкінгу).</p>\n\t\t\t<a class="deciso-brand hidden"')
CSS = """
/* 67space: адаптивна верстка (телефони 360–430 px, ноутбуки) */
html, body { overflow-x: hidden; }
header { height: auto; padding: 28px 16px 0; }
.brand-title { margin: 0; font-size: 34px; font-weight: 700; color: #C03E14; }
.brand-sub { margin: 4px 0 0; font-size: 16px; color: #555; }
.content-row { padding: 32px 0; min-height: calc(100vh - 260px); }
.form-signin { max-width: 360px; margin: 0 auto; padding: 0 16px; box-sizing: border-box; }
.form-signin-heading { font-size: 26px; margin: 0 0 16px; }
input[type="text"], input[type="password"] { width: 100%; box-sizing: border-box; }
.form-signin .btn { display: block; width: 100%; margin-top: 14px; }
.alert { max-width: 360px; margin: 16px auto 0; padding: 0 16px; box-sizing: border-box; font-size: 16px; color: #b00020; }
.help { max-width: 420px; margin: 0 auto; padding: 0 16px; font-size: 13px; color: #666; }
"""


def raw(path, data=None):
    req = urllib.request.Request(f"https://{opn.HOST}{path}")
    req.add_header("Authorization", "Basic " + opn._AUTH)
    with urllib.request.urlopen(req, data, timeout=60, context=opn._CTX) as r:
        return r.read()


def build():
    src = zipfile.ZipFile(io.BytesIO(raw("/api/captiveportal/template/getTemplate/")))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item)
            if item.filename == "index.html":
                html = data.decode("utf-8")
                for old, new in TEXT:
                    assert old in html, f"у шаблоні не знайдено: {old}"
                    html = html.replace(old, new)
                html = html.replace(FOOTER_OLD, FOOTER, 1)
                data = html.encode("utf-8")
            elif item.filename == "css/signin.css":
                data += CSS.encode("utf-8")
            dst.writestr(item, data)
    return out.getvalue()


def install():
    for row in opn.api("/api/captiveportal/template/searchTemplates").get("rows", []):
        if row.get("name") == NAME:
            opn.api(f"/api/captiveportal/template/delTemplate/{row['uuid']}", data={})
    r = opn.api("/api/captiveportal/template/saveTemplate",
                data={"name": NAME, "content": base64.b64encode(build()).decode()})
    uuid = r.get("uuid") or next(row["uuid"] for row in opn.api("/api/captiveportal/template/searchTemplates")["rows"]
                                 if row.get("name") == NAME)
    print("  OK   шаблон", NAME, uuid)
    for row in opn.api("/api/captiveportal/settings/searchZones")["rows"]:
        if "67space" in row["description"]:
            res = opn.api(f"/api/captiveportal/settings/setZone/{row['uuid']}", data={"zone": {"template": uuid}})
            print("  OK  " if res.get("result") == "saved" else "  FAIL", "зона", row["description"], "" if res.get("result") == "saved" else res)
    opn.api("/api/captiveportal/service/reconfigure", data={})
    print("  OK   captive portal застосовано")


if __name__ == "__main__":
    install()
