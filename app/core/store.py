"""账号库：DPAPI 加密的本地存储。

安全设计：
  * 整个库文件用 Windows DPAPI（CurrentUser 作用域）加密，
    换台机器 / 换个 Windows 用户都解不开，纯拷文件偷不到 refresh_token；
  * 每次写盘前自动滚动备份，保留最近 10 份；
  * 导出文件同样加密，可以放心丢网盘同步。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import time
from pathlib import Path

from .identity import Identity

ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "AgSwitch"
STORE = ROOT / "accounts.dat"
BACKUP_DIR = ROOT / "backups"
CACHE_DIR = ROOT / "cache-backups"
MAGIC = b"AGSW1\x00"
ENTROPY = b"AgSwitch/v1"


# ---------------------------------------------------------------- DPAPI

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(_Blob), wt.LPCWSTR, ctypes.POINTER(_Blob),
    ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(_Blob),
]
_crypt32.CryptProtectData.restype = wt.BOOL
_crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(_Blob), ctypes.POINTER(wt.LPWSTR), ctypes.POINTER(_Blob),
    ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(_Blob),
]
_crypt32.CryptUnprotectData.restype = wt.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def dpapi_encrypt(data: bytes) -> bytes:
    din, dent, dout = _blob(data), _blob(ENTROPY), _Blob()
    if not _crypt32.CryptProtectData(ctypes.byref(din), "AgSwitch", ctypes.byref(dent), None, None, 0, ctypes.byref(dout)):
        raise OSError(f"CryptProtectData 失败 {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(dout.pbData, dout.cbData)
    finally:
        _kernel32.LocalFree(dout.pbData)


def dpapi_decrypt(data: bytes) -> bytes:
    din, dent, dout = _blob(data), _blob(ENTROPY), _Blob()
    if not _crypt32.CryptUnprotectData(ctypes.byref(din), None, ctypes.byref(dent), None, None, 0, ctypes.byref(dout)):
        raise OSError(f"CryptUnprotectData 失败 {ctypes.get_last_error()}（不是本机/本用户的加密文件？）")
    try:
        return ctypes.string_at(dout.pbData, dout.cbData)
    finally:
        _kernel32.LocalFree(dout.pbData)


# ---------------------------------------------------------------- 仓库

class StoreError(RuntimeError):
    pass


class Store:
    def __init__(self, path: Path = STORE):
        self.path = path
        self.accounts: list[Identity] = []
        self.active_fp: str = ""
        self.last_error = ""
        self.load()

    # ---- io
    def load(self) -> None:
        self.accounts = []
        self.active_fp = ""
        self.last_error = ""
        if not self.path.exists():
            return
        try:
            raw = self.path.read_bytes()
            if raw.startswith(MAGIC):
                raw = dpapi_decrypt(raw[len(MAGIC):])
            data = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.last_error = f"账号库读取失败（已忽略旧数据）：{exc}"
            return

        self.active_fp = data.get("active") or ""
        self.accounts = [Identity.from_dict(x) for x in data.get("accounts", []) if isinstance(x, dict)]

    def save(self, backup: bool = True) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "savedAt": time.time(),
            "active": self.active_fp,
            "accounts": [a.to_dict() for a in self.accounts],
        }
        blob = dpapi_encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        if backup and self.path.exists():
            try:
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                shutil.copy2(self.path, BACKUP_DIR / f"accounts-{stamp}.dat")
                for old in sorted(BACKUP_DIR.glob("accounts-*.dat"))[:-10]:
                    old.unlink(missing_ok=True)
            except OSError:
                pass

        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_bytes(MAGIC + blob)
        os.replace(tmp, self.path)

    # ---- crud
    def find(self, fp: str) -> Identity | None:
        for a in self.accounts:
            if a.fingerprint == fp:
                return a
        return None

    def upsert(self, ident: Identity) -> tuple[Identity, bool]:
        existing = self.find(ident.fingerprint)
        if existing is None:
            ident.captured_at = ident.captured_at or time.time()
            if not ident.label:
                ident.label = ident.email or f"账号 {len(self.accounts) + 1}"
            if not ident.id_token_email:
                ident.id_token_email = ident.email or ""
            self.accounts.append(ident)
            self.save()
            return ident, True

        existing.token = ident.token or existing.token
        existing.auth_method = ident.auth_method or existing.auth_method
        existing.writer_id = ident.writer_id or existing.writer_id
        existing.account_flags = ident.account_flags or existing.account_flags
        existing.cred_scope = ident.cred_scope or existing.cred_scope
        existing.email = ident.email or existing.email
        # 老版本存下来的记录缺 id_token_email，切回去会丢身份 —— 这里自动补齐
        existing.id_token_email = ident.id_token_email or existing.id_token_email or existing.email
        existing.captured_at = time.time()
        if not existing.label:
            existing.label = existing.email or f"账号 …{existing.fingerprint[-6:]}"
        self.save()
        return existing, False

    def rename(self, fp: str, label: str) -> bool:
        a = self.find(fp)
        if not a:
            return False
        a.label = label.strip() or a.label
        self.save()
        return True

    def remove(self, fp: str) -> bool:
        n = len(self.accounts)
        self.accounts = [a for a in self.accounts if a.fingerprint != fp]
        if len(self.accounts) == n:
            return False
        if self.active_fp == fp:
            self.active_fp = ""
        self.save()
        return True

    def set_active(self, fp: str) -> None:
        self.active_fp = fp
        self.save(backup=False)

    def reorder(self, fp: str, delta: int) -> bool:
        idx = next((i for i, a in enumerate(self.accounts) if a.fingerprint == fp), -1)
        if idx < 0:
            return False
        new = max(0, min(len(self.accounts) - 1, idx + delta))
        if new == idx:
            return False
        self.accounts.insert(new, self.accounts.pop(idx))
        self.save()
        return True

    # ---- 导入导出
    def export_encrypted(self, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "exportedAt": time.time(),
            "accounts": [a.to_dict() for a in self.accounts],
        }
        dest.write_bytes(MAGIC + dpapi_encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
        return dest

    def import_encrypted(self, src: Path) -> int:
        raw = src.read_bytes()
        if raw.startswith(MAGIC):
            raw = dpapi_decrypt(raw[len(MAGIC):])
        data = json.loads(raw.decode("utf-8"))
        added = 0
        for item in data.get("accounts", []):
            if not isinstance(item, dict):
                continue
            ident = Identity.from_dict(item)
            ident.source = "imported"
            self.upsert(ident)
            added += 1
        return added
