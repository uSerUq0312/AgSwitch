"""AgSwitch 业务编排：UI 只调这一层。

功能分三块：
  1. 账号切换（凭据管理器层）
  2. 额度看板（language server 本地 API 层，服务端权威数据）
  3. Gemini 登录（拉起 OAuth，走 LS 自己的登录链路）
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import identity, lsapi, oauth, procs, wslfix
from .store import CACHE_DIR, Store


@dataclass
class Result:
    ok: bool
    message: str
    log: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok, "message": self.message, "log": self.log}
        if self.data:
            out["data"] = self.data
        return out


class Service:
    def __init__(self) -> None:
        self.store = Store()
        self._quota_cache: dict[str, Any] = {"at": 0.0, "value": None}

    # ============================================================ 状态

    def status(self) -> dict[str, Any]:
        live = identity.read_current()
        hub = procs.hub_proc()
        active = self.store.find(self.store.active_fp) if self.store.active_fp else None
        ep = lsapi.find_endpoint()
        mismatch, flagged, real = identity.flag_mismatch(live) if live else (False, "", "")
        return {
            "hubRunning": hub is not None,
            "hubPid": hub.pid if hub else None,
            "hubPort": ep.http_port if ep else None,
            "credPresent": live is not None,
            "liveEmail": real if live else "",
            "liveExpiry": identity.fmt_time(live.expiry) if live else "—",
            "liveExpired": bool(live and live.expiry and live.expiry < time.time()),
            "liveAuthMethod": live.auth_method if live else "",
            "emailMismatch": mismatch,
            "flaggedEmail": flagged,
            "activeFp": self.store.active_fp,
            "activeLabel": active.display if active else "—",
            "accountCount": len(self.store.accounts),
            "storePath": str(self.store.path),
            "storeError": self.store.last_error,
            "lsOnline": ep is not None,
            "lsPid": ep.pid if ep else None,
        }

    def fix_email_flags(self) -> Result:
        log = identity.sync_flags_to_credential()
        if not log:
            return Result(True, "邮箱标记本来就是对的", [])
        return Result(True, "已修正邮箱标记", log)

    def accounts(self) -> list[dict[str, Any]]:
        now = time.time()
        live = identity.read_current()
        live_fp = live.fingerprint if live else ""
        out = []
        for a in self.store.accounts:
            exp = a.expiry
            out.append(
                {
                    "fp": a.fingerprint,
                    "label": a.display,
                    "email": a.email,
                    "authMethod": a.auth_method,
                    "active": a.fingerprint == self.store.active_fp,
                    "expiry": exp,
                    "expired": bool(exp and exp < now),
                    "capturedAt": a.captured_at,
                    "source": a.source,
                    "hasRefresh": bool(a.token.get("refresh_token")),
                    "note": a.note,
                    "isLiveCredential": bool(live_fp) and a.fingerprint == live_fp,
                }
            )
        return out

    # ============================================================ 额度

    def quota(self, force: bool = False, ttl: float = 20.0) -> Result:
        ep = lsapi.find_endpoint()
        if ep is None:
            return Result(
                False,
                "Antigravity 没在跑 —— 额度由 language server 提供，先启动 Antigravity",
                ["提示：LS 会用进程内的新鲜 token 去问 Google，比直接调后端可靠"],
            )

        now = time.time()
        if not force and self._quota_cache["value"] and now - self._quota_cache["at"] < ttl:
            return Result(True, "额度（缓存）", data=self._quota_cache["value"])

        try:
            q = lsapi.fetch_quota(ep)
            acc = lsapi.fetch_account(ep)
        except lsapi.LsApiError as exc:
            return Result(False, f"拉取额度失败：{exc}")

        payload = {"quota": q.to_dict(), "account": acc.to_dict(), "fetchedAt": time.time()}
        self._quota_cache = {"at": now, "value": payload}

        log = []
        t = q.tightest
        if t:
            log.append(f"最紧张：{t.name} 剩 {t.percent}%")
        if q.note:
            log.append(q.note[:160])
        return Result(True, "额度已更新", log, payload)

    # ============================================================ 登录

    def open_login(self, force: bool = False) -> Result:
        """拉起 Antigravity / Gemini 的 OAuth 登录。

        实测：已登录时 LS 的 Login 也要跑约 40~60 秒才返回 hasValidAuth=true，
        所以先打毫秒级的 GetAuthStatus：
          * 未登录 -> 走 Login，拿 auth_url 给用户
          * 已登录 -> 直接汇报，不让你白等
        强制重新授权用 open_login(force=True)。
        """
        ep = lsapi.find_endpoint()
        if ep is None:
            return Result(False, "Antigravity 没在跑，先启动它再拉起登录")

        already = False
        scopes: list[str] = []
        if not force:
            try:
                au = (lsapi.auth_status(ep).get("authResult") or {})
                already = bool(au.get("hasValidAuth"))
                scopes = list(au.get("grantedScopes") or [])
            except lsapi.LsApiError:
                already = False

        if already:
            try:
                acc = lsapi.fetch_account(ep)
            except lsapi.LsApiError:
                acc = lsapi.Account()
            log = [
                f"当前已登录：{acc.email or '（未知邮箱）'}"
                + (f" · {acc.plan}" if acc.plan else ""),
                "已授权 scope：" + ", ".join(s.split("/")[-1] for s in scopes),
                "要换账号请先点「登出」，再点本按钮。",
            ]
            return Result(
                True,
                "已经是登录状态",
                log,
                {"alreadySignedIn": True, "authUrl": "", "scopes": scopes},
            )

        try:
            res = lsapi.authenticate(ep)
        except lsapi.LsApiError as exc:
            return Result(False, str(exc))

        auth = res.get("authResult") or {}
        scopes = list(auth.get("grantedScopes") or [])

        try:
            sess = lsapi.login_session(ep)
        except lsapi.LsApiError:
            sess = {}

        auth_url = sess.get("authUrl") or res.get("authUrl") or ""
        log = []
        if auth_url:
            log.append(f"授权地址：{auth_url}")
        if scopes:
            log.append("已授权 scope：" + ", ".join(s.split("/")[-1] for s in scopes))
        if not auth_url and not auth.get("hasValidAuth"):
            log.append("LS 没有返回授权地址，可能是登录窗口已在 Antigravity 里弹出")
        return Result(
            True,
            "已发起登录，请在弹出的浏览器里完成授权" if auth_url else "已发起登录",
            log,
            {"alreadySignedIn": bool(auth.get("hasValidAuth")), "authUrl": auth_url, "scopes": scopes},
        )

    # ============================================================ 抓取

    def capture_current(self, label: str = "") -> Result:
        live = identity.read_current()
        if live is None:
            return Result(False, "读不到 gemini:antigravity 凭据 —— 先在 Antigravity 里登录一个账号")
        if not live.token.get("refresh_token") and not live.token.get("access_token"):
            return Result(False, "凭据里没有可用 token")

        # 尽量用 LS 的真实账号信息补全邮箱
        ep = lsapi.find_endpoint()
        if ep:
            try:
                acc = lsapi.fetch_account(ep)
                if acc.email:
                    live.email = acc.email
                    live.id_token_email = live.id_token_email or acc.email
                    live.account_flags["agy.onboarding.lastLoginUsername"] = acc.email
            except lsapi.LsApiError:
                pass

        # 老版本存下来的账号可能没有 id_token_email，补一次，否则切回去会丢身份
        if not live.id_token_email:
            live.id_token_email = identity.effective_email(live)

        if label.strip():
            live.label = label.strip()
        saved, is_new = self.store.upsert(live)
        self.store.set_active(saved.fingerprint)

        log = [
            f"账号：{saved.email or '（token 未携带邮箱）'}",
            f"auth_method：{saved.auth_method}",
            f"access_token 到期：{identity.fmt_time(saved.expiry)}",
            f"refresh_token：{'有' if saved.token.get('refresh_token') else '无'}",
        ]
        return Result(True, f"已{'新增' if is_new else '更新'}：{saved.display}", log)

    # ============================================================ 切换

    def switch_to(self, fp: str, clear_session: bool = False, restart: bool = True) -> Result:
        target = self.store.find(fp)
        if target is None:
            return Result(False, "账号不存在，请先抓取")

        log: list[str] = []
        log += identity.apply_identity(target)
        self.store.set_active(fp)

        if clear_session:
            moved = identity.clear_session_cache(CACHE_DIR)
            log.append(f"已清理 {len(moved)} 处会话缓存" if moved else "无会话缓存需要清理")

        if restart:
            log += procs.restart_hub()
            log += procs.restart_ide()

        self._quota_cache = {"at": 0.0, "value": None}
        return Result(True, f"已切换到 {target.display}", log)

    # ============================================================ 其它

    def refresh_account(self, fp: str, client_secret: str = "", client_id: str = "") -> Result:
        a = self.store.find(fp)
        if a is None:
            return Result(False, "账号不存在")
        if not client_secret:
            return Result(
                False,
                "缺少 client_secret（Antigravity 内置 client 均为 Desktop 类型）。"
                "最省事的办法：切过去让 Antigravity 自己续，再点「同步 token」。",
            )
        try:
            fresh = oauth.try_refresh(a.token.get("refresh_token", ""), client_secret, client_id or None)
        except oauth.RefreshError as exc:
            return Result(False, f"刷新失败：{exc}")
        a.token["access_token"] = fresh["access_token"]
        a.token["expiry"] = fresh["expiry"]
        a.token["token_type"] = fresh["token_type"]
        self.store.save()
        return Result(True, "access_token 已刷新", [f"新到期时间 {identity.fmt_time(a.expiry)}"])

    def relogin_live(self) -> Result:
        live = identity.read_current()
        if live is None:
            return Result(False, "当前没有登录态")
        saved, is_new = self.store.upsert(live)
        return Result(True, f"已同步真实登录态到 {saved.display}", [f"到期 {identity.fmt_time(saved.expiry)}"])

    def delete(self, fp: str) -> Result:
        return Result(self.store.remove(fp), "已从账号库移除" if self.store.find(fp) is None else "移除失败")

    def rename(self, fp: str, label: str) -> Result:
        return Result(self.store.rename(fp, label), f"已重命名为 {label}")

    def move(self, fp: str, delta: int) -> Result:
        return Result(self.store.reorder(fp, delta), "已调整顺序")

    def logout(self) -> Result:
        log = identity.logout_from_disk()
        ep = lsapi.find_endpoint()
        if ep:
            try:
                lsapi.logout(ep)
                log.append("已通知 language server 退出登录")
            except lsapi.LsApiError:
                pass
        log += procs.restart_hub()
        self._quota_cache = {"at": 0.0, "value": None}
        return Result(True, "已退出登录", log)

    def export_accounts(self, dest: str) -> Result:
        try:
            p = self.store.export_encrypted(Path(dest))
        except OSError as exc:
            return Result(False, f"导出失败：{exc}")
        return Result(True, f"已导出到 {p}", [f"{len(self.store.accounts)} 个账号，DPAPI 加密"])

    def import_accounts(self, src: str) -> Result:
        try:
            n = self.store.import_encrypted(Path(src))
        except (OSError, ValueError) as exc:
            return Result(False, f"导入失败：{exc}")
        return Result(True, f"已导入 {n} 个账号")

    def diagnose(self) -> Result:
        info = identity.diagnose()
        ep = lsapi.find_endpoint()
        wsl = wslfix.detect()
        lines = [
            f"凭据 {info['credential']['target']}：{'存在' if info['credential']['present'] else '不存在'}",
            f"当前邮箱：{info['credential']['email'] or '（未知）'}",
            f"邮箱标记：{info['credential'].get('flaggedEmail') or '（空）'}"
            + ("  ⚠ 与凭据不一致" if info["credential"].get("mismatch") else ""),
            f"auth_method：{info['credential']['authMethod'] or '—'}",
            f"access_token 到期：{info['credential']['expiry'] or '—'}"
            + ("  ⚠ 已过期（LS 用内存态续期，额度仍可用）" if info["credential"]["expired"] else ""),
            f"language server：{'在线 ' + str(ep.http_port) + ' (pid ' + str(ep.pid) + ')' if ep else '离线'}",
            f"WSL：{'已启用' if wsl.installed else '未启用 —— Antigravity 启动时会刷 WSL 报错（不影响功能）'}",
        ]
        for issue in procs.diagnose_launch_blockers():
            lines.append(f"⚠ 启动隐患：{issue}")
        for d in info["stateDirs"]:
            lines.append(
                f"状态目录 {d['path']}：storage={'有' if d['hasStorage'] else '无'} "
                f"scope={d['scope'] or '—'} lastLogin={d['lastLogin'] or '—'}"
            )
        return Result(True, "体检完成", lines, data={**info, "wsl": wsl.to_dict()})

    # ============================================================ WSL 报错处置

    def wsl_status(self) -> Result:
        st = wslfix.detect()
        stats = wslfix.log_stats()
        main = stats.get("main", {})
        log = [
            f"wsl.exe：{'存在' if st.wsl_exe_present else '不存在'}",
            f"WslService：{'存在' if st.service_present else '不存在'}",
            f"新版安装（C:\\Program Files\\WSL）：{'有' if st.modern_install else '无'}",
            f"分发版：{', '.join(st.distros) if st.distros else '（无）'}",
            f"结论：{'WSL 可用' if st.installed else (st.error or 'WSL 不可用')}",
            "",
            f"main.log：{main.get('lines', 0)} 行 / {main.get('bytes', 0) // 1024} KB，"
            f"其中 WSL 报错 {main.get('wslErrors', 0)} 处"
            if main.get("exists")
            else "main.log：未找到",
            "",
            "这两类报错只是启动探测失败，登录 / 额度 / 对话全都不受影响。",
            "根因：app.asar 的 wsl.js 用绝对路径 C:\\Windows\\System32\\wsl.exe 去列分发版，",
            "      没装 WSL 就会失败。PATH 劫持对它无效（实测），改 SystemRoot 会让 Electron 起不来。",
            "根治只有一条正路：" + wslfix.install_command(),
        ]
        return Result(True, "WSL 状态", log, data={"state": st.to_dict(), "logs": stats})

    def wsl_install_command(self) -> Result:
        lines = wslfix.admin_instruction()
        lines.append("")
        lines.append("不想装 WSL 也完全没问题 —— 这些报错是无害的，可以直接忽略。")
        return Result(True, "根治命令（需要管理员）", lines)

    def wsl_clean_logs(self) -> Result:
        ok, log = wslfix.suppress_log_noise()
        return Result(ok, "日志已清理" if ok else "未清理", log)
