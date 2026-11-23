"""AgSwitch 入口：起后端 + 开窗口。

窗口优先级：
  1. WebView2 原生窗口（pythonnet + Microsoft.Web.WebView2，本机自带 Edge 运行时即可）
  2. Edge/Chrome 的 --app 模式（独立窗口，无地址栏）
  3. 直接打印带 token 的 URL，手动打开
"""
from __future__ import annotations

import os
import sys
import time

# 控制台可能是 GBK/936，中文标题会乱码；能改就改成 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# 防坑：如果从某些工具（如 DSH / 打包脚本）继承了 ELECTRON_RUN_AS_NODE=1，
# 我们拉起的 Antigravity.exe 会以 Node 模式运行然后秒退，看起来像"程序坏了"。
# 这里是本进程自己的环境，清掉它，子进程就不会继承。
if os.environ.get("ELECTRON_RUN_AS_NODE"):
    os.environ.pop("ELECTRON_RUN_AS_NODE", None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import api as api_mod          # noqa: E402
from app.core import bridge, shell_window    # noqa: E402

APP_TITLE = "AgSwitch · Antigravity 账号切换"


def main() -> int:
    flags = {a.lower() for a in sys.argv[1:]}
    headless = "--headless" in flags or "--serve" in flags
    browser = "--browser" in flags

    srv, api = api_mod.start_background()
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}/#t={api.token}"

    st = api.svc.status()
    print(APP_TITLE)
    print(f"  服务地址 : http://127.0.0.1:{port}/")
    print(f"  Hub      : {'运行中 pid ' + str(st['hubPid']) if st['hubRunning'] else '未运行'}")
    print(f"  当前凭据 : {'有 · ' + (st['liveEmail'] or '未知邮箱') if st['credPresent'] else '无'}")
    print(f"  账号库   : {st['accountCount']} 个 · {st['storePath']}")
    if st.get("storeError"):
        print(f"  警告     : {st['storeError']}")
    sys.stdout.flush()

    if headless:
        print("\n无头模式，Ctrl+C 退出。")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    if not browser:
        try:
            if bridge.run_window(APP_TITLE, url):
                return 0
        except Exception as exc:      # noqa: BLE001
            print(f"WebView2 窗口不可用，改用浏览器窗口：{exc}")

    try:
        shell_window.open_app_window(url)
    except Exception as exc:          # noqa: BLE001
        print(f"打不开窗口（{exc}）\n请手动访问：{url}")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
