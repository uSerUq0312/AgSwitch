"""AgSwitch 后端 HTTP API。

只监听 127.0.0.1 + 系统分配端口，所有 /api/* 需要一个一次性 token
（放在 URL fragment 里传，不进 Referer、不进服务端日志）。
"""
from __future__ import annotations

import json
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import paths
from .service import Result, Service

WEB_DIR = paths.web_dir()
MAX_BODY = 1 << 20


class Api:
    def __init__(self) -> None:
        self.svc = Service()
        self.token = secrets.token_urlsafe(24)


def _send_json(h: BaseHTTPRequestHandler, code: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Content-Type-Options", "nosniff")
    h.end_headers()
    h.wfile.write(body)


def _send_file(h: BaseHTTPRequestHandler, path: Path) -> None:
    if not path.is_file():
        h.send_error(404)
        return
    ctype = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
    }.get(path.suffix.lower(), "application/octet-stream")
    data = path.read_bytes()
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(data)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(data)


def make_handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AgSwitch/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        # -------- 安全头
        def _common_headers(self) -> None:
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")

        def _authed(self) -> bool:
            supplied = self.headers.get("X-AgSwitch-Token", "")
            return bool(supplied) and secrets.compare_digest(supplied, api.token)

        # -------- GET
        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return _send_file(self, WEB_DIR / "index.html")
            if path.startswith("/static/"):
                target = (WEB_DIR / path[len("/static/"):]).resolve()
                if WEB_DIR.resolve() not in target.parents:
                    return self.send_error(403)
                return _send_file(self, target)
            if path.startswith("/api/"):
                if not self._authed():
                    return _send_json(self, 401, {"ok": False, "message": "unauthorized"})
                if path == "/api/state":
                    return _send_json(
                        self, 200, {"ok": True, "status": api.svc.status(), "accounts": api.svc.accounts()}
                    )
                if path == "/api/quota":
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    force = qs.get("force", ["0"])[0] in ("1", "true")
                    return _send_json(self, 200, api.svc.quota(force=force).to_dict())
                if path == "/api/diagnose":
                    return _send_json(self, 200, api.svc.diagnose().to_dict())
                if path == "/api/wsl":
                    return _send_json(self, 200, api.svc.wsl_status().to_dict())
            return self.send_error(404)

        # -------- POST
        def do_POST(self):
            if not self._authed():
                return _send_json(self, 401, {"ok": False, "message": "unauthorized"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return _send_json(self, 400, {"ok": False, "message": "bad content-length"})
            if length > MAX_BODY:
                return _send_json(self, 413, {"ok": False, "message": "body too large"})
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
                if not isinstance(body, dict):
                    raise ValueError
            except (UnicodeDecodeError, ValueError):
                return _send_json(self, 400, {"ok": False, "message": "bad json"})

            path = urlparse(self.path).path
            svc = api.svc

            def out(r):
                return _send_json(self, 200 if r.ok else 400, r.to_dict())

            if path == "/api/capture":
                return out(svc.capture_current(str(body.get("label") or "")))
            if path == "/api/quota":
                return out(svc.quota(force=bool(body.get("force"))))
            if path == "/api/login":
                return out(svc.open_login(force=bool(body.get("force"))))
            if path == "/api/fix-email":
                return out(svc.fix_email_flags())
            if path == "/api/switch":
                return out(
                    svc.switch_to(
                        str(body.get("fp") or ""),
                        clear_session=bool(body.get("clearSession", False)),
                        restart=bool(body.get("restart", True)),
                    )
                )
            if path == "/api/refresh":
                return out(
                    svc.refresh_account(
                        str(body.get("fp") or ""),
                        str(body.get("clientSecret") or ""),
                        str(body.get("clientId") or ""),
                    )
                )
            if path == "/api/relogin":
                return out(svc.relogin_live())
            if path == "/api/delete":
                return out(svc.delete(str(body.get("fp") or "")))
            if path == "/api/rename":
                return out(svc.rename(str(body.get("fp") or ""), str(body.get("label") or "")))
            if path == "/api/move":
                return out(svc.move(str(body.get("fp") or ""), int(body.get("delta") or 0)))
            if path == "/api/logout":
                return out(svc.logout())
            if path == "/api/export":
                return out(svc.export_accounts(str(body.get("path") or "")))
            if path == "/api/import":
                return out(svc.import_accounts(str(body.get("path") or "")))
            if path == "/api/diagnose":
                return out(svc.diagnose())
            if path == "/api/wsl":
                return out(svc.wsl_status())
            if path == "/api/wsl/install-shim":
                return out(Result(False, "垫片方案已废弃：PATH 劫持对它无效（它用绝对路径）"))
            if path == "/api/wsl/remove-shim":
                return out(Result(True, "垫片方案已废弃，无需清理"))
            if path == "/api/wsl/command":
                return out(svc.wsl_install_command())
            if path == "/api/wsl/clean-logs":
                return out(svc.wsl_clean_logs())
            return self.send_error(404)

    return Handler


def start_background() -> tuple[ThreadingHTTPServer, Api]:
    api = Api()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    srv.daemon_threads = True
    import threading

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, api
