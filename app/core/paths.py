"""统一解析资源路径：源码运行 vs PyInstaller 打包后运行。

PyInstaller 把 --add-data 的内容解到 sys._MEIPASS（onefile）或
<exe 同级>_internal（onedir）。web 目录必须两种都能找到。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """web/ 等只读资源所在根目录。"""
    if is_frozen():
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
        # onedir：exe 同级
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def web_dir() -> Path:
    return resource_root() / "web"


def data_root() -> Path:
    """可写数据目录。打包后不要写到 Program Files。"""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    p = Path(base) / "AgSwitch"
    p.mkdir(parents=True, exist_ok=True)
    return p
