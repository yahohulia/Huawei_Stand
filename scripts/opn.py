"""Мінімальний клієнт REST API OPNsense для стенда 67space.
Ключ читається з secrets/OPNsense.internal_root_apikey.txt (у git/здачу не додавати).
Сертифікат VM самопідписаний, тому перевірка TLS вимкнена — лише для лабораторної VM.
"""
import base64
import json
import os
import re
import ssl
import urllib.request

HOST = os.environ.get("OPN_HOST", "192.168.56.10")
KEYFILE = os.path.join(os.path.dirname(__file__), "..", "secrets", "OPNsense.internal_root_apikey.txt")

_t = open(KEYFILE, encoding="utf-8").read()
_AUTH = base64.b64encode((re.search(r"key=(\S+)", _t).group(1) + ":" +
                          re.search(r"secret=(\S+)", _t).group(1)).encode()).decode()
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def api(path, data=None, method=None, timeout=60):
    """api('/api/core/firmware/info') -> dict. Із data виконується POST."""
    req = urllib.request.Request(f"https://{HOST}{path}", method=method or ("POST" if data is not None else "GET"))
    req.add_header("Authorization", "Basic " + _AUTH)
    body = None
    if data is not None:
        req.add_header("Content-Type", "application/json")
        body = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(req, body, timeout=timeout, context=_CTX) as r:
            raw = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read().decode("utf-8", "replace"), e.code
    try:
        out = json.loads(raw)
    except ValueError:
        out = {"_raw": raw[:300]}
    if isinstance(out, dict):
        out["_http"] = code
    return out
