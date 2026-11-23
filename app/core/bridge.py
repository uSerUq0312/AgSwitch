"""可选增强：如果用户装了 pythonnet + WebView2，就用原生 WinForms 窗口。

没装也不影响使用（shell_window 用 Edge --app 兜底）。
"""
from __future__ import annotations

import os
import sys


def available() -> bool:
    try:
        import clr  # noqa: F401
        import Microsoft.Web.WebView2.Core  # noqa: F401
        import Microsoft.Web.WebView2.WinForms  # noqa: F401
    except Exception:
        return False
    return True


def _add_dll_dirs() -> None:
    base = os.path.dirname(sys.executable)
    for sub in (".", r"..\Lib\site-packages\pythonnet\runtime", r"..\Lib\site-packages\clr_loader\ffi\dlls"):
        p = os.path.abspath(os.path.join(base, sub))
        if os.path.isdir(p):
            try:
                os.add_dll_directory(p)
            except (OSError, AttributeError):
                pass


def run_window(title: str, url: str, width: int = 1120, height: int = 780) -> bool:
    if not available():
        return False
    _add_dll_dirs()

    import Microsoft.Web.WebView2.Core as wv2core
    import Microsoft.Web.WebView2.WinForms as wv2wf
    import System.Windows.Forms as Forms
    from System import Action
    from System.Drawing import Color, Size
    from System.Threading import ApartmentState, Thread, ThreadStart
    from System.Threading.Tasks import Task

    state = {"started": False}

    def _resolve(sender, args):
        try:
            if args is not None and str(args.Name).startswith("System.Windows.Forms"):
                args.ReturnValue = wv2wf
                args.Handled = True
        except Exception:
            pass

    try:
        Forms.AssemblyResolve += _resolve
    except Exception:
        pass

    def ui() -> None:
        app = Forms.Application
        app.EnableVisualStyles()
        app.SetCompatibleTextRenderingDefault(False)

        win = Forms.Form()
        win.Text = title
        win.Width = width
        win.Height = height
        win.MinimumSize = Size(900, 620)
        win.StartPosition = Forms.FormStartPosition.CenterScreen
        win.BackColor = Color.FromArgb(11, 13, 18)

        web = wv2wf.WebView2()
        web.Dock = Forms.DockStyle.Fill
        win.Controls.Add(web)

        def boot() -> None:
            try:
                env = wv2core.CoreWebView2Environment.CreateAsync(None, None, None).Result
                core = web.EnsureCoreWebView2Async(env).Result
                core.Settings.AreDefaultContextMenusEnabled = False
                core.Settings.IsStatusBarEnabled = False
                core.Navigate(url)
                state["started"] = True
            except Exception as exc:  # noqa: BLE001
                try:
                    win.Text = f"{title} — WebView2 初始化失败：{exc}"
                except Exception:
                    pass

        win.Shown += lambda s, e: (win.Activate(), Task.Run(Action(boot)))
        app.Run(win)

    t = Thread(ThreadStart(Action(ui)))
    t.SetApartmentState(ApartmentState.STA)
    t.Start()
    t.Join()
    return True
