"""Windows 凭据管理器（Credential Manager）读写封装。

Antigravity 的登录凭据就存在这里：
    TargetName : gemini:antigravity
    UserName   : antigravity
    Type       : CRED_TYPE_GENERIC (1)
    Persist    : CRED_PERSIST_LOCAL_MACHINE (2)
    Blob       : UTF-8 JSON
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import time
from typing import Any, Optional

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
CRED_PERSIST_SESSION = 1
CRED_PERSIST_ENTERPRISE = 3

_ERROR_NOT_FOUND = 1168


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wt.DWORD),
        ("Type", wt.DWORD),
        ("TargetName", wt.LPWSTR),
        ("Comment", wt.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wt.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wt.DWORD),
        ("AttributeCount", wt.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wt.LPWSTR),
        ("UserName", wt.LPWSTR),
    ]


_advapi = ctypes.WinDLL("advapi32", use_last_error=True)
_advapi.CredReadW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
_advapi.CredReadW.restype = wt.BOOL
_advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wt.DWORD]
_advapi.CredWriteW.restype = wt.BOOL
_advapi.CredDeleteW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD]
_advapi.CredDeleteW.restype = wt.BOOL
_advapi.CredFree.argtypes = [ctypes.c_void_p]


class CredError(RuntimeError):
    pass


def _last_error() -> int:
    return ctypes.get_last_error()


def read_raw(target: str, cred_type: int = CRED_TYPE_GENERIC) -> Optional[bytes]:
    """读取一条凭据的原始 blob；不存在返回 None。"""
    p = ctypes.POINTER(_CREDENTIAL)()
    if not _advapi.CredReadW(target, cred_type, 0, ctypes.byref(p)):
        err = _last_error()
        if err == _ERROR_NOT_FOUND:
            return None
        raise CredError(f"CredReadW({target}) 失败，Windows 错误码 {err}")
    try:
        c = p.contents
        return bytes(bytearray(c.CredentialBlob[: c.CredentialBlobSize]))
    finally:
        _advapi.CredFree(p)


def write_raw(
    target: str,
    blob: bytes,
    username: str = "",
    comment: str = "",
    cred_type: int = CRED_TYPE_GENERIC,
    persist: int = CRED_PERSIST_LOCAL_MACHINE,
) -> None:
    """写入/覆盖一条通用凭据。"""
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    now = int(time.time() * 10_000_000) + 116444736000000000
    cred = _CREDENTIAL()
    cred.Flags = 0
    cred.Type = cred_type
    cred.TargetName = target
    cred.Comment = comment
    cred.LastWritten = _FILETIME(now & 0xFFFFFFFF, now >> 32)
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = persist
    cred.AttributeCount = 0
    cred.Attributes = None
    cred.TargetAlias = None
    cred.UserName = username

    if not _advapi.CredWriteW(ctypes.byref(cred), 0):
        raise CredError(f"CredWriteW({target}) 失败，Windows 错误码 {_last_error()}")


def delete(target: str, cred_type: int = CRED_TYPE_GENERIC) -> bool:
    if _advapi.CredDeleteW(target, cred_type, 0):
        return True
    err = _last_error()
    if err == _ERROR_NOT_FOUND:
        return False
    raise CredError(f"CredDeleteW({target}) 失败，Windows 错误码 {err}")


def read_json(target: str) -> Optional[dict[str, Any]]:
    raw = read_raw(target)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CredError(f"凭据 {target} 不是合法 UTF-8 JSON：{exc}") from exc


def write_json(target: str, data: dict[str, Any], username: str = "", **kw) -> None:
    blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    write_raw(target, blob, username=username, **kw)
