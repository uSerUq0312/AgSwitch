"""启动器验证：跑 .cmd 的 --headless 模式，抓输出、按多编码解码、确认能起来。

用法：python tests/test_launcher.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHERS = ["start-agswitch.cmd", "启动 AgSwitch.cmd"]


def decode(raw: bytes) -> str:
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            text = raw.decode(enc)
            # 判据：能解出中文字符就认为成功
            if any("\u4e00" <= ch <= "\u9fff" for ch in text) or enc == "latin-1":
                return text
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def run_one(name: str) -> tuple[bool, list[str]]:
    exe = ROOT / name
    if not exe.exists():
        return False, [f"{name}: 文件不存在"]

    log = Path(tempfile.gettempdir()) / f"agsw_launch_{abs(hash(name))}.log"
    with open(log, "wb") as fh:
        proc = subprocess.Popen(
            ["cmd.exe", "/c", str(exe), "--headless"],
            cwd=str(ROOT),
            stdout=fh,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(7)
        alive = proc.poll() is None
        if alive:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        proc.wait(timeout=10)

    # 顺手收掉 python 子进程
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'app\\\\main.py.*headless' } "
         "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
        capture_output=True,
    )

    raw = log.read_bytes()
    text = decode(raw)
    lines = [ln for ln in text.replace("\r", "").split("\n") if ln.strip()]

    ok = True
    problems: list[str] = []
    if "Using interpreter" not in text:
        ok = False
        problems.append("没有打印解释器路径（可能没找到 Python）")
    else:
        py_line = next((ln for ln in lines if "Using interpreter" in ln), "")
        path = py_line.split(":", 1)[-1].strip()
        if not os.path.exists(path):
            ok = False
            problems.append(f"解释器路径不存在：{path}")
    if "服务地址" not in text:
        ok = False
        problems.append("没看到启动成功的『服务地址』行")
    if "不是内部或外部命令" in text:
        ok = False
        problems.append("仍然出现命令解析错误")
    if "\ufffd" in text:
        problems.append("输出里有替换字符（可能编码不匹配，但不致命）")

    return ok, problems + lines[:8]


def main() -> int:
    all_ok = True
    for name in LAUNCHERS:
        print(f"\n=== {name} ===")
        ok, lines = run_one(name)
        all_ok = all_ok and ok
        print(f"  [{'PASS' if ok else 'FAIL'}]")
        for ln in lines:
            print(f"      {ln}")
    print("\n" + ("全部通过" if all_ok else "存在失败项"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
