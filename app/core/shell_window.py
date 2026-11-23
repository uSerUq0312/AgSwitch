"""Chromium app 窗口：用系统 Edge/Chrome 打开一个无地址栏的独立窗口。

不引入任何第三方依赖；WebView2 不可用时这是默认宿主。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def find_browser() -> str | None:
    for p in CANDIDATES:
        if os.path.exists(p):
            return p
    for name in ("msedge.exe", "chrome.exe"):
        p = shutil.which(name)
        if p:
            return p
    return None


def profile_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "AgSwitch"
    p = base / "window-profile"
    p.mkdir(parents=True, exist_ok=True)
    return p


def open_app_window(url: str, width: int = 1120, height: int = 800, wait: bool = True) -> int:
    exe = find_browser()
    if not exe:
        raise RuntimeError("找不到 Microsoft Edge / Chrome")
    args = [
        exe,
        f"--app={url}",
        f"--user-data-dir={profile_dir()}",
        f"--window-size={width},{height}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,msEdgeSplitScreen,msEdgeSidebarV2",
        "--disable-extensions",
        "--disable-background-networking",
    ]
    proc = subprocess.Popen(args, close_fds=True)
    if wait:
        proc.wait()
    return proc.pid
