"""用 CDP 给 AgSwitch 窗口截图，验证界面真的渲染出来了。

需要一个带 --remote-debugging-port 的 Edge 实例。这里自己起一份最小实例：
  1. 用同一个 profile 打开 AgSwitch 页面（带调试端口）
  2. 连 CDP -> Page.captureScreenshot
  3. 保存 PNG
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = r"C:\Users\UserQu\AppData\Local\AgSwitch\tests\shot.png"
DBG_PORT = 9333


def launch(url: str) -> subprocess.Popen:
    profile = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgSwitch", "cdp-profile"
    )
    os.makedirs(profile, exist_ok=True)
    args = [
        EDGE,
        f"--app={url}",
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={DBG_PORT}",
        "--window-size=1120,860",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--headless=new",
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cdp_targets() -> list[dict]:
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{DBG_PORT}/json/list", timeout=2) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(0.25)
    return []


def screenshot(ws_url: str, delay: float = 2.5) -> bytes:
    from websocket import create_connection  # type: ignore

    ws = create_connection(ws_url, timeout=20)
    try:
        ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        ws.recv()
        time.sleep(delay)
        ws.send(json.dumps({"id": 2, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 2:
                return base64.b64decode(msg["result"]["data"])
    finally:
        ws.close()


def main() -> int:
    url = sys.argv[1]
    delay = float(sys.argv[2]) if len(sys.argv) > 2 else 2.5
    proc = launch(url)
    try:
        targets = cdp_targets()
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            print("找不到页面 target：", targets[:3])
            return 1
        ws_url = pages[0]["webSocketDebuggerUrl"]
        try:
            png = screenshot(ws_url, delay=delay)
        except ImportError:
            print("缺少 websocket-client，跳过截图")
            return 2
        with open(OUT, "wb") as f:
            f.write(png)
        print("saved", OUT, len(png), "bytes")
        return 0
    finally:
        proc.terminate()
        time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
