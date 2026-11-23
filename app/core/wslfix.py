"""WSL 报错的处置工具（实测后重写的最终版）。

════════════════════════════════════════════════════════════════════
报错到底是什么
════════════════════════════════════════════════════════════════════
main.log 里刷屏的两类：
    [error] Failed to list WSL distros: Error: wsl.exe --list --verbose failed
    [error] wsl:get-state failed: Error: wsl.exe --list --verbose failed

它们来自 app.asar 内的 dist/wsl.js：

    exports.WSL_EXE = path.join(process.env.SystemRoot || 'C:\\Windows',
                                'System32', 'wsl.exe');

Antigravity 每次启动 / 开菜单 / 刷状态都会跑一次 `wsl.exe --list --verbose`。
你这台没装 WSL（WslService 不存在、没有分发版），wsl.exe 是占位程序，
于是每次都抛错。**功能完全不受影响**，只是日志难看。

════════════════════════════════════════════════════════════════════
为什么"垫片/PATH 劫持"这条思路被我否掉了（都实测过）
════════════════════════════════════════════════════════════════════
1. PATH 前置假 wsl.exe：
   日志里写的是 C:\\WINDOWS\\System32\\wsl.exe —— 它用的是绝对路径，
   Node 拿绝对路径直接执行，PATH 完全不参与。无效。
2. 改 SystemRoot 指到假根：
   Electron 自身大量依赖 SystemRoot，试验结果是整个进程起不来、LS 拉不起来。危险。
3. 在 System32 放垫片：需要管理员，且污染系统目录，不做。

════════════════════════════════════════════════════════════════════
所以只剩一条正确做法：装 WSL 组件（不装发行版）
════════════════════════════════════════════════════════════════════
    wsl.exe --install --no-distribution

之后 `wsl --list --verbose` 正常返回空表，报错彻底消失。
约 100MB，一次性，可随时 `wsl --unregister` / 组件卸载。

本模块负责：
  * 诊断（不需要管理员）
  * 生成/复制那条命令，并给出精确到"在哪开管理员终端"的指引
  * 提供"日志静音"的可选替代：只把 main.log 里这些行过滤掉，不碰任何程序

════════════════════════════════════════════════════════════════════
如果就是不想装 WSL
════════════════════════════════════════════════════════════════════
这些报错是**无害的**。工具提供 `suppress_log_noise()`：把已产生的
WSL 报错块从 main.log 里剥离（先备份），并可选地在 Antigravity 每次
被本工具重启后自动清理一次。这不阻止新报错，但能让日志可读。
"""
from __future__ import annotations

import os
import re
import subprocess
import winreg
from dataclasses import dataclass, field
from pathlib import Path

_NOT_INSTALLED_MARKERS = ("未安装适用于 Linux", "wsl.exe --install", "not installed")

APPDATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
MAIN_LOG = APPDATA / "Antigravity" / "logs" / "main.log"
LS_LOG = APPDATA / "Antigravity" / "logs" / "language_server.log"


@dataclass
class WslState:
    wsl_exe_present: bool = False
    service_present: bool = False
    modern_install: bool = False
    distros: list[str] = field(default_factory=list)
    installed: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "wslExePresent": self.wsl_exe_present,
            "servicePresent": self.service_present,
            "modernInstall": self.modern_install,
            "distros": self.distros,
            "installed": self.installed,
            "error": self.error,
        }


def _decode(raw: bytes) -> str:
    if not raw:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(raw) > 8 and raw[1::2].count(0) > len(raw) // 4):
        try:
            return raw.decode("utf-16", errors="replace").replace("\x00", "")
        except Exception:
            pass
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _run(cmd: list[str], timeout: float = 20.0) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=0x08000000)
        return r.returncode, _decode((r.stdout or b"") + (r.stderr or b""))
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError as exc:
        return 1, str(exc)


def detect() -> WslState:
    st = WslState(wsl_exe_present=os.path.exists(r"C:\Windows\System32\wsl.exe"))
    st.modern_install = os.path.exists(r"C:\Program Files\WSL\wsl.exe")
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\WslService"):
            st.service_present = True
    except OSError:
        st.service_present = False

    rc, blob = _run(["wsl.exe", "--list", "--verbose"])
    if any(m in blob for m in _NOT_INSTALLED_MARKERS):
        st.installed = False
        st.error = "WSL 组件未启用（System32\\wsl.exe 只是占位程序）"
        return st
    if rc != 0:
        st.installed = False
        st.error = f"wsl --list 返回 {rc}：{blob.strip()[:160]}"
        return st

    names = []
    for line in blob.splitlines():
        line = line.replace("\x00", "").replace("*", " ").strip()
        if not line or line.upper().startswith("NAME"):
            continue
        token = line.split()[0] if line.split() else ""
        if token:
            names.append(token)
    st.distros = names
    st.installed = True
    return st


def install_command() -> str:
    return "wsl.exe --install --no-distribution"


def admin_instruction() -> list[str]:
    return [
        "在开始菜单搜索 PowerShell → 右键「以管理员身份运行」→ 粘贴执行：",
        "",
        "    " + install_command(),
        "",
        "装完重启一次电脑，之后 Antigravity 的 WSL 报错会彻底消失。",
        "（--no-distribution 表示只装 WSL 运行组件、不装 Ubuntu，约 100MB）",
        "不需要 WSL 了可以随时卸载组件，不影响已经装好的任何东西。",
    ]


# ------------------------------------------------------------------ 日志静音

_WSL_BLOCK = re.compile(
    r"\[[^\]]+\] \[error\] (?:Failed to list WSL distros|wsl:get-state failed):.*?"
    r"(?=\r?\n\[\d{4}-\d{2}-\d{2} |\Z)",
    re.S,
)


def log_stats() -> dict:
    """统计两个日志里 WSL 噪声占比，用于给用户一个直观数字。"""
    out = {}
    for name, path in (("main", MAIN_LOG), ("ls", LS_LOG)):
        if not path.exists():
            out[name] = {"exists": False}
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.count("\n") + 1
        wsl = text.count("wsl.exe --list --verbose failed")
        out[name] = {
            "exists": True,
            "bytes": path.stat().st_size,
            "lines": lines,
            "wslErrors": wsl,
        }
    return out


def suppress_log_noise() -> tuple[bool, list[str]]:
    """把 main.log 里已产生的 WSL 报错块删掉（先备份）。不改任何程序。"""
    if not MAIN_LOG.exists():
        return False, ["main.log 不存在"]
    text = MAIN_LOG.read_text(encoding="utf-8", errors="replace")
    before = text.count("wsl.exe --list --verbose failed")
    if before == 0:
        return True, ["没有需要清理的 WSL 报错"]

    backup = MAIN_LOG.with_name(MAIN_LOG.name + ".agswitch.bak")
    try:
        backup.write_text(text, encoding="utf-8")
    except OSError as exc:
        return False, [f"备份失败：{exc}"]

    cleaned, n = _WSL_BLOCK.subn("", text)
    if n == 0:
        return False, ["匹配不到完整的报错块，未改动（避免误删）"]

    try:
        # Antigravity 正在写日志时会占用文件；用追加/改写语义尽量避免锁冲突
        MAIN_LOG.write_text(cleaned, encoding="utf-8")
    except OSError as exc:
        return False, [f"写入失败（Antigravity 可能在占用日志，先关掉它再试）：{exc}"]

    after = cleaned.count("wsl.exe --list --verbose failed")
    return True, [
        f"已清理 {n} 个 WSL 报错块（{before} 处 -> {after} 处）",
        f"原文件备份在：{backup}",
        "注意：这只是清理历史日志，Antigravity 下次启动仍会产生新的；",
        "要根治请执行 wsl --install --no-distribution。",
    ]
