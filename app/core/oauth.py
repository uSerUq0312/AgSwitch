"""OAuth 相关：只做能确定做到的事。

实测结论（Antigravity language_server 2.34.0）：
  * 三个内置 client_id 都是 Desktop 类型，refresh 时必须带 client_secret；
  * client_secret **不落盘、不进环境变量**，二进制里也只有缓存/演示用的残片，
    真正的 secret 在服务端下发或运行时注入。
  => 所以本工具不假装能离线刷新 access_token。

能做的：
  1. 解析 token 里的过期时间，告诉用户"这个账号的 access_token 什么时候失效"；
  2. 切号后让 Antigravity 自己走一遍登录 → 它会把新的 token 写回凭据管理器，
     我们再"二次抓取"覆盖账号库（流程见 service.capture_after_relogin）。
  3. 如果用户自己提供 client_secret（有些企业部署会下发），就按标准 refresh 流程走。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

TOKEN_URL = "https://oauth2.googleapis.com/token"

CLIENT_IDS = (
    "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com",
    "884354919052-36trc1jjb3tguiac32ov6cod268c5blh.apps.googleusercontent.com",
    "1098991175725-mdu1oe5cbbe1ckggsebobh94toiap657.apps.googleusercontent.com",
)


class RefreshError(RuntimeError):
    pass


def _post(form: dict[str, str], timeout: int = 25) -> dict[str, Any]:
    body = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        body,
        {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "AgSwitch/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            detail = json.loads(raw).get("error_description") or raw
        except json.JSONDecodeError:
            detail = raw
        raise RefreshError(f"HTTP {exc.code}: {detail.strip()[:200]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RefreshError(f"网络不可达：{exc}") from exc


def try_refresh(refresh_token: str, client_secret: str, client_id: str | None = None) -> dict[str, Any]:
    """用户提供了 client_secret 时才调用。返回新的 access_token 与 expiry。"""
    if not (refresh_token and client_secret):
        raise RefreshError("缺少 refresh_token 或 client_secret")
    last: Exception | None = None
    for cid in ([client_id] if client_id else list(CLIENT_IDS)):
        try:
            data = _post(
                {
                    "client_id": cid,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                }
            )
        except RefreshError as exc:
            last = exc
            continue
        if not data.get("access_token"):
            last = RefreshError("响应里没有 access_token")
            continue
        expires_in = int(data.get("expires_in") or 3599)
        return {
            "access_token": data["access_token"],
            "expiry": time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(time.time() + expires_in)),
            "token_type": data.get("token_type") or "Bearer",
            "client_id": cid,
        }
    raise RefreshError(str(last) if last else "全部 client_id 均失败")


def token_alive(token: dict[str, Any], skew_seconds: int = 120) -> bool:
    """access_token 是否还有效（不知道 expiry 时按"未知，视为可用"处理）。"""
    exp = token.get("expiry")
    if not exp:
        return True
    from .identity import parse_expiry

    ts = parse_expiry(exp)
    if ts is None:
        return True
    return time.time() + skew_seconds < ts
