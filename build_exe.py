"""一键打包成单文件 exe。

用法：
    python build_exe.py

产物：dist/AgSwitch.exe
之后可手动拷到桌面，或加 --desktop 直接复制到桌面。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "AgSwitch"


def main() -> int:
    to_desktop = "--desktop" in sys.argv

    if shutil.which("pyinstaller") is None and subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--version"], capture_output=True
    ).returncode != 0:
        print("[x] 没装 PyInstaller：pip install pyinstaller")
        return 1

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name", NAME,
        # 界面资源必须打进去
        "--add-data", f"{ROOT / 'app' / 'web'}{';' if sys.platform == 'win32' else ':'}web",
        # 图标存在就用（可选）
        *(["--icon", str(ROOT / "icon.ico")] if (ROOT / "icon.ico").exists() else []),
        # 只收集标准库，排除用不到的大件，减小体积
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        "--exclude-module", "xmlrpc",
        str(ROOT / "app" / "main.py"),
    ]
    print(" ".join(args))
    rc = subprocess.run(args, cwd=str(ROOT)).returncode
    if rc != 0:
        print(f"[x] 打包失败，退出码 {rc}")
        return rc

    exe = ROOT / "dist" / f"{NAME}.exe"
    if not exe.exists():
        print("[x] 没找到产物 dist/AgSwitch.exe")
        return 1
    size_mb = exe.stat().st_size / 1024 / 1024
    print(f"[ok] {exe}  ({size_mb:.1f} MB)")

    if to_desktop:
        desktop = Path.home() / "Desktop"
        if not desktop.exists():
            import ctypes.wintypes  # noqa: F401
            from ctypes import windll
            buf = ctypes.create_unicode_buffer(260)
            windll.shell32.SHGetFolderPathW(None, 0, None, 0, buf)
            desktop = Path(buf.value)
        dst = desktop / f"{NAME}.exe"
        shutil.copy2(exe, dst)
        print(f"[ok] 已复制到桌面：{dst}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
