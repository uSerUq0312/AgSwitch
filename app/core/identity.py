"""Antigravity 账号身份层。

════════════════════════════════════════════════════════════════════
1. 真正的身份：一条 Windows 凭据
════════════════════════════════════════════════════════════════════
凭据管理器里：
    Target : gemini:antigravity
    User   : antigravity
    Blob   : UTF-8 JSON
             {
               "token": {
                 "access_token":  "ya29....",     # ~1 小时过期
                 "token_type":    "Bearer",
                 "refresh_token": "1//0e...",     # 长期有效
                 "expiry":        "2026-11-21T05:15:14.0537836Z"
               },
               "auth_method": "consumer",         # 或 business / api-key 等
               "writer_id":   "C3o8MOVt4zHB..."   # LS 侧写入者标识
             }

language_server.exe 启动时 CredRead 这条，进程内自己用 refresh_token 续期，
续期后 CredWrite 写回原条目。所以：
    ⇢ 换掉这条 blob = 换掉账号；
    ⇢ 换完必须重启 LS，让它重新读一次。

════════════════════════════════════════════════════════════════════
2. 界面上的账号名：Hub 的本地标记
════════════════════════════════════════════════════════════════════
%APPDATA%\\Antigravity\\app_storage.json
    agy.onboarding.lastLoginUsername   <- 登录时写的邮箱
    agy.onboarding.lastLoginIsGcpTos   <- 是否勾过 GCP TOS
%APPDATA%\\Antigravity\\credential-user-scope
    udc8a7cca0dd5db54                  <- 凭据作用域标识
（Hub 有 launcher 变体，目录名小写 antigravity，两份都要覆盖）
不同步这几个字段的话：token 换了，登录页/头像还显示上一个邮箱。

════════════════════════════════════════════════════════════════════
3. 会话级缓存（换号时按需清理）
════════════════════════════════════════════════════════════════════
Partitions\\hub-browser-*/Network\\Cookies    <- Hub 内嵌浏览器的 Cookie
Network\\Trust Tokens                        <- Chromium trust token
这些不是主身份文件，但清掉能避免"刚换完还闪一下旧账号"。清理前一律备份。
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import wincred

CRED_TARGET = "gemini:antigravity"
CRED_USER = "antigravity"

HOME = Path(os.path.expanduser("~"))
APPDATA = Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local"))

# 同一套身份的多个落点
STATE_DIRS = (APPDATA / "Antigravity", APPDATA / "antigravity")
IDE_STATE_DIRS = (APPDATA / "Antigravity IDE", APPDATA / "antigravity-ide")
DAEMON_ROOTS = (HOME / ".gemini" / "antigravity" / "daemon", HOME / ".gemini" / "antigravity-ide" / "daemon")

APP_STORAGE = "app_storage.json"
CRED_SCOPE = "credential-user-scope"

ACCOUNT_KEYS = ("agy.onboarding.lastLoginUsername", "agy.onboarding.lastLoginIsGcpTos")

# 换号可选的会话缓存，相对 Hub 数据目录
SESSION_CACHE_GLOBS = (
    "Partitions/hub-browser-*/Network/Cookies*",
    "Network/Cookies*",
    "Network/Trust Tokens*",
    "Local Storage/leveldb",
    "Session Storage",
)


# ---------------------------------------------------------------- 小工具

def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".agswitch.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def backup_file(path: Path, tag: str = "agswitch") -> Path | None:
    if not path.exists():
        return None
    dest = path.with_name(f"{path.name}.{tag}.{time.strftime('%Y%m%d-%H%M%S')}.bak")
    try:
        shutil.copy2(path, dest)
        return dest
    except OSError:
        return None


def state_dirs(include_ide: bool = True) -> list[Path]:
    dirs = [d for d in STATE_DIRS if d.exists()]
    if include_ide:
        dirs += [d for d in IDE_STATE_DIRS if d.exists()]
    return dirs


def discover_daemon_files() -> list[Path]:
    out: list[Path] = []
    for root in DAEMON_ROOTS:
        if root.exists():
            out += sorted(root.glob("hub_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return out


# ---------------------------------------------------------------- 解析

_EXPIRY_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?"
)


def parse_expiry(raw: Any) -> float | None:
    """Google 的 expiry 形如 2026-11-21T05:15:14.0537836Z。

    Google 给的是 UTC，小数位可能是 7 位（strptime 的 %f 只吃 1~6 位），
    所以这里用正则拆字段再自己算 epoch，不走 strptime。
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        return v / 1000 if v > 1e11 else v

    s = str(raw).strip()
    m = _EXPIRY_RE.search(s)
    if not m:
        return None

    year, mon, day, hh, mm, ss = (int(m.group(i)) for i in range(1, 7))
    frac = m.group(7) or "0"
    micro = int((frac + "000000")[:6])
    tz = m.group(8)

    dt = datetime.datetime(year, mon, day, hh, mm, ss, micro, tzinfo=datetime.timezone.utc)
    ts = dt.timestamp()
    if tz and tz not in ("Z", "z"):
        sign = 1 if tz[0] == "+" else -1
        body = tz[1:].replace(":", "")
        offset = int(body[:2]) * 3600 + int(body[2:4] or 0) * 60
        ts -= sign * offset
    return ts


def fmt_time(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"


def decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return {}
    try:
        seg = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(seg))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------- 模型

@dataclass
class Identity:
    label: str = ""
    email: str = ""
    token: dict[str, Any] = field(default_factory=dict)
    auth_method: str = "consumer"
    writer_id: str = ""
    account_flags: dict[str, Any] = field(default_factory=dict)
    cred_scope: str = ""
    captured_at: float = 0.0
    source: str = "captured"
    note: str = ""
    id_token_email: str = ""       # 凭据里的 id_token_email，身份权威字段

    @property
    def fingerprint(self) -> str:
        """账号指纹：优先 refresh_token 尾部，退化为 access_token 尾部。"""
        rt = str(self.token.get("refresh_token") or "")
        at = str(self.token.get("access_token") or "")
        return (rt or at)[-24:] or "unknown"

    @property
    def expiry(self) -> float | None:
        return parse_expiry(self.token.get("expiry"))

    @property
    def display(self) -> str:
        return self.label or self.email or f"账号 …{self.fingerprint[-6:]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "email": self.email,
            "token": self.token,
            "auth_method": self.auth_method,
            "writer_id": self.writer_id,
            "account_flags": self.account_flags,
            "cred_scope": self.cred_scope,
            "captured_at": self.captured_at,
            "source": self.source,
            "note": self.note,
            "id_token_email": self.id_token_email,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Identity":
        return cls(
            label=d.get("label") or "",
            email=d.get("email") or "",
            token=d.get("token") or {},
            auth_method=d.get("auth_method") or "consumer",
            writer_id=d.get("writer_id") or "",
            account_flags=d.get("account_flags") or {},
            cred_scope=d.get("cred_scope") or "",
            captured_at=float(d.get("captured_at") or 0),
            source=d.get("source") or "captured",
            note=d.get("note") or "",
            id_token_email=d.get("id_token_email") or "",
        )


# ---------------------------------------------------------------- 读当前

def read_current() -> Identity | None:
    blob = wincred.read_json(CRED_TARGET)
    if not blob or not isinstance(blob.get("token"), dict):
        return None

    ident = Identity(
        token=dict(blob["token"]),
        auth_method=blob.get("auth_method") or "consumer",
        writer_id=blob.get("writer_id") or "",
        id_token_email=str(blob.get("id_token_email") or "").strip(),
        captured_at=time.time(),
        source="captured",
    )

    for d in state_dirs(include_ide=False):
        storage = d / APP_STORAGE
        data = read_json(storage)
        if data:
            ident.account_flags = {k: data[k] for k in ACCOUNT_KEYS if k in data}
        scope = read_text(d / CRED_SCOPE)
        if scope:
            ident.cred_scope = scope
        if ident.account_flags or scope:
            break

    # 邮箱优先级：凭据里的 id_token_email > app_storage 标记 > token 内解析
    #
    # 为什么 id_token_email 最权威：app_storage 的 lastLoginUsername 是"上次登录时写下的"，
    # 一旦账号被换掉/LS 自己续期换号，这里不会同步更新 —— 实测踩过：
    # 凭据已经是 size18foxmail@gmail.com，app_storage 还留着旧邮箱。
    ident.email = (
        ident.id_token_email
        or str(ident.account_flags.get("agy.onboarding.lastLoginUsername") or "").strip()
        or _email_from_token(ident.token)
    )
    return ident


def effective_email(ident: Identity) -> str:
    """这个身份"真正属于谁"的邮箱，用于判断界面显示与凭据是否一致。"""
    return ident.id_token_email or _email_from_token(ident.token) or ident.email


def flag_mismatch(ident: Identity) -> tuple[bool, str, str]:
    """检测 app_storage 标记与凭据身份是否错位。返回 (是否错位, 标记邮箱, 真实邮箱)。"""
    real = effective_email(ident)
    flagged = str(ident.account_flags.get("agy.onboarding.lastLoginUsername") or "").strip()
    if real and flagged and real.lower() != flagged.lower():
        return True, flagged, real
    return False, flagged, real


def _email_from_token(token: dict[str, Any]) -> str:
    for key in ("id_token", "access_token"):
        payload = decode_jwt_payload(str(token.get(key) or ""))
        for f in ("email", "preferred_username"):
            v = payload.get(f)
            if isinstance(v, str) and "@" in v:
                return v
    return ""


# ---------------------------------------------------------------- 写回

def apply_identity(ident: Identity, sync_flags: bool = True) -> list[str]:
    """把某个账号写回系统。

    写回策略：**保留结构、替换身份**。
      * 结构（writer_id / expires_in 等 LS 关心的字段）从旧 blob 继承，避免有损；
      * 身份字段（token 四件套 + id_token_email）必须来自目标账号，
        否则切到 B 时会把 A 的 id_token_email 留给 B —— 那等于"人是 B、名牌是 A"，
        同步标记时还会把 B 的邮箱强行改成 A。

    踩过的两个坑都在这：
      1. 只写自己认识的字段 → id_token_email 被冲成空串；
      2. 无条件继承旧 id_token_email → 切号后身份错认。
    """
    log: list[str] = []

    old = wincred.read_json(CRED_TARGET) or {}
    old_token = old.get("token") if isinstance(old.get("token"), dict) else {}

    new_token = dict(old_token)
    new_token.update(
        {
            "access_token": str(ident.token.get("access_token") or ""),
            "token_type": ident.token.get("token_type") or "Bearer",
            "refresh_token": str(ident.token.get("refresh_token") or ""),
            "expiry": ident.token.get("expiry") or "",
        }
    )
    # expires_in 只有和 refresh 结果配套时才有意义；目标账号没带就别继承旧的
    if ident.token.get("expires_in"):
        new_token["expires_in"] = ident.token["expires_in"]
    else:
        new_token.pop("expires_in", None)

    blob: dict[str, Any] = dict(old)
    blob["token"] = new_token
    blob["auth_method"] = ident.auth_method or old.get("auth_method") or "consumer"

    # 目标账号没有 writer_id 才复用旧的（它更像"客户端标识"而非"账号标识"）
    blob["writer_id"] = ident.writer_id or old.get("writer_id") or ""

    # 身份邮箱：严格用目标账号自己的
    email = ident.id_token_email or _email_from_token(ident.token) or ""
    if email:
        blob["id_token_email"] = email
    else:
        blob.pop("id_token_email", None)

    wincred.write_json(CRED_TARGET, blob, username=CRED_USER)
    log.append(f"凭据管理器已写入 {CRED_TARGET}（{len(json.dumps(blob))} 字节）" + (f"，身份 {email}" if email else ""))

    if not sync_flags:
        return log

    for d in state_dirs():
        storage = d / APP_STORAGE
        if storage.exists():
            data = read_json(storage)
            changed = False
            for k, v in (ident.account_flags or {}).items():
                if data.get(k) != v:
                    data[k] = v
                    changed = True
            if changed:
                backup_file(storage)
                write_text_atomic(storage, json.dumps(data, ensure_ascii=False, indent=2))
                log.append(f"账号标记已更新 {storage}")

        scope_file = d / CRED_SCOPE
        if ident.cred_scope and scope_file.exists() and read_text(scope_file) != ident.cred_scope:
            backup_file(scope_file)
            write_text_atomic(scope_file, ident.cred_scope + "\n")
            log.append(f"凭据作用域已切换 {scope_file}")

    return log


def clear_session_cache(backup_dir: Path) -> list[Path]:
    """把 Hub 的会话级缓存挪到备份目录（可还原），返回清掉的文件。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    moved: list[Path] = []
    for d in state_dirs():
        for pattern in SESSION_CACHE_GLOBS:
            for target in d.glob(pattern):
                if not target.exists():
                    continue
                dest = backup_dir / stamp / d.name / target.relative_to(d)
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), str(dest))
                    moved.append(target)
                except OSError:
                    continue
    return moved


def logout_from_disk() -> list[str]:
    """等价于 Antigravity 的"退出登录"：删凭据 + 清掉邮箱标记。"""
    log: list[str] = []
    if wincred.delete(CRED_TARGET):
        log.append(f"已删除凭据 {CRED_TARGET}")
    for d in state_dirs():
        storage = d / APP_STORAGE
        data = read_json(storage)
        if data.get("agy.onboarding.lastLoginUsername"):
            backup_file(storage)
            data["agy.onboarding.lastLoginUsername"] = ""
            write_text_atomic(storage, json.dumps(data, ensure_ascii=False, indent=2))
            log.append(f"已清空邮箱标记 {storage}")
    return log


def sync_flags_to_credential(ident: Identity | None = None) -> list[str]:
    """把 app_storage 的邮箱标记对齐到凭据里的真实身份。

    经典场景：Antigravity 里换了账号 / LS 自己续期时把凭据换成了另一个账号，
    但 app_storage 的 lastLoginUsername 还停在旧邮箱 —— 界面于是显示错人。
    """
    ident = ident or read_current()
    if ident is None:
        return []
    real = effective_email(ident)
    if not real:
        return []

    log: list[str] = []
    for d in state_dirs():
        storage = d / APP_STORAGE
        if not storage.exists():
            continue
        data = read_json(storage)
        if not data:
            continue
        if str(data.get("agy.onboarding.lastLoginUsername") or "").lower() == real.lower():
            continue
        backup_file(storage)
        data["agy.onboarding.lastLoginUsername"] = real
        write_text_atomic(storage, json.dumps(data, ensure_ascii=False, indent=2))
        log.append(f"已把 {storage} 的邮箱标记改成 {real}")
    return log


def diagnose() -> dict[str, Any]:
    """给 UI 的"体检"数据。"""
    cur = read_current()
    dirs = state_dirs()
    mismatch, flagged, real = flag_mismatch(cur) if cur else (False, "", "")
    return {
        "credential": {
            "target": CRED_TARGET,
            "present": cur is not None,
            "email": real if cur else "",
            "flaggedEmail": flagged,
            "mismatch": mismatch,
            "authMethod": cur.auth_method if cur else "",
            "expiry": fmt_time(cur.expiry) if cur else "",
            "expired": bool(cur and cur.expiry and cur.expiry < time.time()),
            "hasRefresh": bool(cur and cur.token.get("refresh_token")),
        },
        "stateDirs": [
            {
                "path": str(d),
                "hasStorage": (d / APP_STORAGE).exists(),
                "scope": read_text(d / CRED_SCOPE),
                "lastLogin": read_json(d / APP_STORAGE).get("agy.onboarding.lastLoginUsername", ""),
            }
            for d in dirs
        ],
        "daemon": [str(p) for p in discover_daemon_files()[:4]],
    }
