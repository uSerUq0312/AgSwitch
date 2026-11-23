"""进程控制：重启 Antigravity Hub / IDE，并在重启前清掉"会话级"缓存。

要点（实测）：
  * Hub = Antigravity.exe（Electron 主进程），它拉起 resources\\bin\\language_server.exe；
  * token 是 language_server 启动时从凭据管理器读的，所以换完 token 必须重启它；
  * 只重启 LS 不够：Electron 侧的 renderer 还缓存着旧账号态，
    所以按默认策略直接重启整个 Antigravity.exe。
  * 绝对不能碰 t3code / codex / 别的 Electron 应用。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008

HUB_EXE_NAMES = {"antigravity.exe"}
IDE_EXE_NAMES = {"antigravity ide.exe"}


@dataclass
class ProcInfo:
    pid: int
    name: str
    exe: str = ""

    @property
    def lname(self) -> str:
        return self.name.lower()

    @property
    def is_hub(self) -> bool:
        return self.lname in HUB_EXE_NAMES

    @property
    def is_ide(self) -> bool:
        return self.lname in IDE_EXE_NAMES

    @property
    def is_ls(self) -> bool:
        return self.lname == "language_server.exe"

    @property
    def is_renderer(self) -> bool:
        return self.lname == "antigravity.exe" and "--type=" in self.exe.lower()


def _ps(script: str, timeout: int = 25) -> str:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
        return r.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def list_procs() -> list[ProcInfo]:
    out = _ps(
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'Antigravity|language_server' } | "
        "ForEach-Object { \"$($_.ProcessId)`t$($_.Name)`t$($_.ExecutablePath)`t$($_.CommandLine)\" }"
    )
    procs: list[ProcInfo] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not parts[0].strip().isdigit():
            continue
        cmd = parts[3] if len(parts) > 3 else ""
        procs.append(ProcInfo(int(parts[0]), parts[1].strip(), (parts[2].strip() + " " + cmd).strip()))
    return procs


def hub_proc() -> ProcInfo | None:
    """真正的 Hub 主进程（排除 renderer/gpu 子进程）。"""
    for p in list_procs():
        if p.is_hub and "--type=" not in p.exe:
            return p
    return None


def ide_proc() -> ProcInfo | None:
    for p in list_procs():
        if p.is_ide and "--type=" not in p.exe:
            return p
    return None


def _exe_of(p: ProcInfo) -> str:
    raw = p.exe.split(" ")[0] if p.exe else ""
    return raw if raw.lower().endswith(".exe") else ""


def _kill(pid: int, force: bool) -> None:
    args = ["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else [])
    subprocess.run(args, capture_output=True, creationflags=CREATE_NO_WINDOW)


def _wait_gone(proc_name: set[str], timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not any(p.lname in proc_name for p in list_procs()):
            return True
        time.sleep(0.35)
    return False


def _launch(exe: str) -> None:
    env = dict(os.environ)
    # 从工具环境（如 DSH）继承来的 ELECTRON_RUN_AS_NODE=1 会让 Antigravity
    # 以 Node 模式启动并秒退，必须清掉
    env.pop("ELECTRON_RUN_AS_NODE", None)
    subprocess.Popen(
        [exe],
        cwd=str(Path(exe).parent),
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
        env=env,
    )


def diagnose_launch_blockers() -> list[str]:
    """启动 Antigravity 前检查那些"看起来像程序坏了"的环境问题。"""
    issues: list[str] = []
    if os.environ.get("ELECTRON_RUN_AS_NODE"):
        issues.append(
            "环境变量 ELECTRON_RUN_AS_NODE=1 存在：会让 Antigravity 以 Node 模式启动后秒退"
        )
    return issues


def restart_hub(graceful: float = 6.0) -> list[str]:
    log: list[str] = []
    proc = hub_proc()
    if proc is None:
        log.append("Antigravity Hub 未运行，跳过重启")
        return log

    exe = _exe_of(proc)
    log.append(f"关闭 Antigravity Hub（pid {proc.pid}）")
    _kill(proc.pid, force=False)
    if not _wait_gone(HUB_EXE_NAMES, graceful):
        log.append("优雅退出超时，强制结束")
        _kill(proc.pid, force=True)
        _wait_gone(HUB_EXE_NAMES, 4.0)

    if exe and os.path.exists(exe):
        _launch(exe)
        log.append(f"已重新启动 {exe}")
    else:
        log.append("拿不到 Antigravity.exe 路径，请手动启动 Hub")
    return log


def restart_ide(graceful: float = 6.0) -> list[str]:
    log: list[str] = []
    proc = ide_proc()
    if proc is None:
        return log
    exe = _exe_of(proc)
    log.append(f"关闭 Antigravity IDE（pid {proc.pid}）")
    _kill(proc.pid, force=False)
    if not _wait_gone(IDE_EXE_NAMES, graceful):
        _kill(proc.pid, force=True)
        _wait_gone(IDE_EXE_NAMES, 4.0)
    if exe and os.path.exists(exe):
        _launch(exe)
        log.append(f"已重新启动 {exe}")
    return log


def hub_running() -> bool:
    return hub_proc() is not None


def hub_endpoint() -> dict | None:
    """Hub 的 daemon 发现文件：~/.gemini/antigravity/daemon/hub_<httpPort>.json"""
    from .identity import discover_daemon_files, read_json

    live_pids = {p.pid for p in list_procs()}
    for f in discover_daemon_files():
        data = read_json(f)
        if data and data.get("pid") in live_pids:
            data["_file"] = str(f)
            return data
    return None
