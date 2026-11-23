"""探针：Antigravity 的 OAuth client_secret 到底有没有落盘。

结论用于决定 AgSwitch 的刷新策略：
  * 能在本地抓到 -> 直接本地换 access_token，切换零等待；
  * 抓不到     -> 退回"切号后由 Hub 自己走登录/刷新"，工具只负责写回 token。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import re
from pathlib import Path

SECRET_RE = re.compile(rb"GOCSPX-[A-Za-z0-9_\-]{28,40}")
CLIENT_RE = re.compile(rb"[0-9]{6,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com")

ROOTS = [
    Path(os.environ.get("APPDATA", "")),
    Path(os.environ.get("LOCALAPPDATA", "")),
]

SKIP_DIRS = {"Cache", "Code Cache", "GPUCache", "GPUPersistentCache", "GrShaderCache",
             "ShaderCache", "DawnGraphiteCache", "DawnWebGPUCache", "Service Worker",
             "node_modules", "blob_storage", "Crashpad", "logs"}


def scan_files(limit_mb: int = 8) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    seen: set[str] = set()
    for root in ROOTS:
        if not root or not root.exists():
            continue
        for dp, dns, fns in os.walk(root):
            p = Path(dp)
            low = str(p).lower()
            if not any(k in low for k in ("antigravity", "gemini", "jetski", "codeium")):
                continue
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            for fn in fns:
                f = p / fn
                try:
                    if f.stat().st_size > limit_mb * 1024 * 1024:
                        continue
                    blob = f.read_bytes()
                except OSError:
                    continue
                for m in SECRET_RE.finditer(blob):
                    s = m.group().decode(errors="ignore")
                    if s not in seen:
                        seen.add(s)
                        hits.append((str(f), s))
    return hits


def scan_env() -> dict[str, str]:
    keys = ("GOOGLE", "OAUTH", "ANTIGRAVITY", "AGY", "GEMINI", "CLOUD_CODE")
    return {k: v for k, v in os.environ.items() if any(t in k.upper() for t in keys)}


if __name__ == "__main__":
    print("== 环境变量 ==")
    for k, v in scan_env().items():
        print(f"  {k}={v[:60]}")

    print("\n== 落盘 secret ==")
    found = scan_files()
    if not found:
        print("  未发现 GOCSPX- 形式的 client_secret")
    for path, s in found:
        print(f"  {path} -> {s[:16]}…")

    print("\n== 配置中的 client_id ==")
    for root in ROOTS:
        for dp, dns, fns in os.walk(root):
            if not any(k in dp.lower() for k in ("antigravity", "gemini")):
                continue
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            for fn in fns:
                f = Path(dp) / fn
                try:
                    if f.stat().st_size > 4 * 1024 * 1024:
                        continue
                    txt = f.read_bytes()
                except OSError:
                    continue
                for m in CLIENT_RE.finditer(txt):
                    print(f"  {f} -> {m.group().decode()}")
