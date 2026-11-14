"""Validate local VLESS/REALITY profiles and protect the node list with user-scoped DPAPI."""
import base64
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit
from uuid import UUID


class Blob(c.Structure):
    _fields_ = [("size", w.DWORD), ("data", c.POINTER(c.c_ubyte))]


def protect(data, decrypt=False):
    source_buffer = c.create_string_buffer(data, len(data))
    source = Blob(len(data), c.cast(source_buffer, c.POINTER(c.c_ubyte)))
    result = Blob()
    api = c.WinDLL("crypt32", use_last_error=True)
    fn = api.CryptUnprotectData if decrypt else api.CryptProtectData
    fn.argtypes = [c.POINTER(Blob), c.c_void_p, c.POINTER(Blob), c.c_void_p, c.c_void_p, w.DWORD, c.POINTER(Blob)]
    free = c.WinDLL("kernel32").LocalFree
    free.argtypes = [w.HLOCAL]
    free.restype = w.HLOCAL
    if not fn(c.byref(source), None, None, None, None, 1, c.byref(result)):
        raise ValueError("Windows could not unlock the profile using this user account")
    try:
        return c.string_at(result.data, result.size)
    finally:
        c.memset(result.data, 0, result.size)
        free(c.cast(result.data, w.HLOCAL))
        c.memset(source_buffer, 0, len(data))


def _profile(line):
    uri = urlsplit(line)
    if (len(line)>4096 or uri.scheme!="vless" or not uri.port or uri.password is not None
            or uri.path not in ("","/") or not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}",uri.hostname or "")):
        raise ValueError()
    identity = str(UUID(uri.username or ""))
    query = parse_qs(uri.query, keep_blank_values=True, strict_parsing=True)
    fields = {"security","type","transport","flow","sni","pbk","sid","fp","spx","encryption","headerType"}
    if set(query)-fields or any(len(v)!=1 for v in query.values()):
        raise ValueError()
    q = {k: v[0] for k,v in query.items()}
    if (q.get("security")!="reality" or q.get("type","tcp") not in ("tcp","raw")
            or q.get("transport","tcp") not in ("tcp","raw") or q.get("encryption","none")!="none"
            or q.get("flow","") not in ("","xtls-rprx-vision") or q.get("headerType","none")!="none"):
        raise ValueError()
    if (not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}",q.get("sni",""))
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}",q.get("pbk",""))
            or not re.fullmatch(r"(?:[0-9A-Fa-f]{2}){0,8}",q.get("sid",""))
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}",q.get("fp","chrome"))
            or any(ord(ch)<32 for ch in q.get("spx","/"))):
        raise ValueError()
    return {"address":uri.hostname,"port":uri.port,"id":identity,"flow":q.get("flow",""),
            "reality":{"serverName":q["sni"],"publicKey":q["pbk"],"shortId":q.get("sid",""),
                       "fingerprint":q.get("fp","chrome"),"spiderX":q.get("spx","/")}}


def from_file(path):
    path=Path(path)
    if not path.is_file() or path.stat().st_size>32768:
        raise ValueError("Provide a local text file with one to four node links")
    try:
        lines=[s.strip() for s in path.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    except UnicodeError:
        raise ValueError("Save the node file as UTF-8") from None
    if not 1<=len(lines)<=4:
        raise ValueError("Provide one primary and up to three backup links, in priority order")
    nodes=[]
    for index,line in enumerate(lines,1):
        try:
            node=_profile(line)
            if node in nodes:
                raise ValueError()
        except (ValueError, IndexError, UnicodeError):
            raise ValueError("Invalid, unsupported or duplicate REALITY TCP profile on line "+str(index)) from None
        nodes.append(node)
    return nodes


def encrypt(nodes):
    return base64.b64encode(protect(json.dumps(nodes).encode("utf-8"))).decode("ascii")


def decrypt(ciphertext):
    return json.loads(protect(base64.b64decode(ciphertext,validate=True),decrypt=True))
