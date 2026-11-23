"""Antigravity language server 本地 API 客户端。

════════════════════════════════════════════════════════════════════
为什么走这条路
════════════════════════════════════════════════════════════════════
直接拿 access_token 打 Google 后端（cloudcode-pa）需要 token 是新鲜的，
而 token 的续期和 client_secret 都攥在 language server 手里（见 oauth.py 的说明）。

但 LS 自己同时起了一个本地 HTTP 服务，做了三件事：
  1. 用进程内的新鲜 token 去请求后端；
  2. 把结果原样转给本地调用方；
  3. 结果里就包含额度、用户状态、模型列表。

于是只要 LS 在跑，AgSwitch 就能拿到**服务端权威额度**，不用碰 token。

════════════════════════════════════════════════════════════════════
实测接口（language_server 2.34.0，POST + JSON）
════════════════════════════════════════════════════════════════════
POST /exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary  {}
POST /exa.language_server_pb.LanguageServerService/GetUserStatus              {}
POST /exa.language_server_pb.LanguageServerService/GetAuthStatus              {}
POST /exa.language_server_pb.LanguageServerService/GetTokenBase               {}
POST /exa.language_server_pb.LanguageServerService/HasAuthToken              {}

鉴权头：X-Codeium-Csrf-Token = daemon/hub_<port>.json 里的 csrfToken
（不带头也能调，但带上更稳，跟 Hub 自己的调用方式一致）
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from . import identity, procs

SVC = "/exa.language_server_pb.LanguageServerService/"


class LsApiError(RuntimeError):
    pass


# ------------------------------------------------------------------ 端点发现

@dataclass
class Endpoint:
    http_port: int
    csrf: str = ""
    pid: int = 0

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.http_port}"


def find_endpoint() -> Endpoint | None:
    """从 ~/.gemini/antigravity/daemon/hub_<port>.json 找活着的 LS。"""
    live = {p.pid for p in procs.list_procs()}
    for f in identity.discover_daemon_files():
        data = identity.read_json(f)
        if not data:
            continue
        pid = data.get("pid")
        port = data.get("httpPort")
        if not port:
            continue
        if live and pid not in live:
            continue
        return Endpoint(http_port=int(port), csrf=str(data.get("csrfToken") or ""), pid=int(pid or 0))
    return None


def probe(port: int, timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/healthz")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


# ------------------------------------------------------------------ 调用

def call(ep: Endpoint, method: str, body: dict[str, Any] | None = None, timeout: float = 25.0) -> dict[str, Any]:
    raw = json.dumps(body or {}).encode()
    headers = {"Content-Type": "application/json"}
    if ep.csrf:
        headers["X-Codeium-Csrf-Token"] = ep.csrf
        headers["X-Codeium-UI-Token"] = ep.csrf
    req = urllib.request.Request(ep.base + SVC + method, raw, headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise LsApiError(f"{method} -> HTTP {exc.code}: {detail.strip()}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise LsApiError(f"{method} -> 连不上 language server：{exc}") from exc
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LsApiError(f"{method} -> 返回不是 JSON：{text[:160]}") from exc


# ------------------------------------------------------------------ 业务封装

@dataclass
class Bucket:
    bucket_id: str
    name: str
    window: str
    remaining: float          # 0..1
    reset_at: str = ""
    disabled: bool = False
    description: str = ""

    @property
    def percent(self) -> int:
        return max(0, min(100, round(self.remaining * 100)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.bucket_id,
            "name": self.name,
            "window": self.window,
            "remaining": round(self.remaining, 4),
            "percent": self.percent,
            "resetAt": self.reset_at,
            "disabled": self.disabled,
            "description": self.description,
        }


@dataclass
class Group:
    name: str
    description: str = ""
    buckets: list[Bucket] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "buckets": [b.to_dict() for b in self.buckets],
        }


@dataclass
class Quota:
    groups: list[Group] = field(default_factory=list)
    note: str = ""
    fetched_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "groups": [g.to_dict() for g in self.groups],
            "note": self.note,
            "fetchedAt": self.fetched_at,
        }

    @property
    def tightest(self) -> Bucket | None:
        """最紧张的那一桶（最低剩余比例，跳过 disabled）。"""
        cands = [b for g in self.groups for b in g.buckets if not b.disabled]
        return min(cands, key=lambda b: b.remaining) if cands else None


def fetch_quota(ep: Endpoint) -> Quota:
    data = call(ep, "RetrieveUserQuotaSummary")
    resp = data.get("response") or {}
    groups: list[Group] = []
    for g in resp.get("groups") or []:
        buckets = []
        for b in g.get("buckets") or []:
            try:
                remaining = float(b.get("remainingFraction", 1))
            except (TypeError, ValueError):
                remaining = 1.0
            buckets.append(
                Bucket(
                    bucket_id=str(b.get("bucketId") or ""),
                    name=str(b.get("displayName") or b.get("bucketId") or ""),
                    window=str(b.get("window") or ""),
                    remaining=remaining,
                    reset_at=str(b.get("resetTime") or ""),
                    disabled=bool(b.get("disabled")),
                    description=str(b.get("description") or ""),
                )
            )
        groups.append(
            Group(
                name=str(g.get("displayName") or "配额"),
                description=str(g.get("description") or ""),
                buckets=buckets,
            )
        )
    return Quota(groups=groups, note=str(resp.get("description") or ""))


@dataclass
class Account:
    email: str = ""
    name: str = ""
    plan: str = ""
    tier: str = ""
    prompt_credits_left: int | None = None
    prompt_credits_total: int | None = None
    flow_credits_left: int | None = None
    flow_credits_total: int | None = None
    has_valid_auth: bool = False
    granted_scopes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "name": self.name,
            "plan": self.plan,
            "tier": self.tier,
            "promptCreditsLeft": self.prompt_credits_left,
            "promptCreditsTotal": self.prompt_credits_total,
            "flowCreditsLeft": self.flow_credits_left,
            "flowCreditsTotal": self.flow_credits_total,
            "hasValidAuth": self.has_valid_auth,
            "grantedScopes": self.granted_scopes,
        }


def fetch_account(ep: Endpoint) -> Account:
    """账号信息。优先问 LS（它手里是真实的在线身份），拿不到再退回凭据文件。"""
    acc = Account()

    try:
        st = (call(ep, "GetUserStatus").get("userStatus") or {})
    except LsApiError:
        st = {}

    acc.email = str(st.get("email") or "")
    acc.name = str(st.get("name") or "")
    plan_status = st.get("planStatus") or {}
    plan_info = plan_status.get("planInfo") or {}
    acc.plan = str(plan_info.get("planName") or "")
    acc.tier = str(plan_info.get("teamsTier") or "")
    acc.prompt_credits_total = _int(plan_info.get("monthlyPromptCredits"))
    acc.flow_credits_total = _int(plan_info.get("monthlyFlowCredits"))
    acc.prompt_credits_left = _int(plan_status.get("availablePromptCredits"))
    acc.flow_credits_left = _int(plan_status.get("availableFlowCredits"))

    try:
        au = (call(ep, "GetAuthStatus").get("authResult") or {})
    except LsApiError:
        au = {}
    acc.has_valid_auth = bool(au.get("hasValidAuth"))
    acc.granted_scopes = list(au.get("grantedScopes") or [])

    return acc


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def authenticate(ep: Endpoint, timeout: float = 90.0) -> dict[str, Any]:
    """触发 LS 拉起自己的登录流程（浏览器 OAuth）。

    实测注意：已登录时 LS 仍会走一遍内部校验，**耗时可达 40~60 秒**才返回
    hasValidAuth=true；只有真的未登录时才会快速给出 auth_url。
    所以这里用长超时，并在调用方做异步化，避免 UI 卡住。
    """
    try:
        return call(ep, "Login", {}, timeout=timeout)
    except LsApiError as exc:
        raise LsApiError(f"拉起登录失败：{exc}") from exc


def auth_status(ep: Endpoint) -> dict[str, Any]:
    """毫秒级，用于先判断"是否已登录"，避免无谓地等 Login 那 40 秒。"""
    return call(ep, "GetAuthStatus", {}, timeout=10)


def login_session(ep: Endpoint) -> dict[str, Any]:
    return call(ep, "GetLoginSession")


def logout(ep: Endpoint) -> dict[str, Any]:
    return call(ep, "AuthLogout")
