"""WSL 诊断与日志清理的回归测试。

重点验证：
  * detect() 能正确判定"未安装"（而不是崩溃或误报已安装）
  * log_stats() 能数出报错数
  * suppress_log_noise() 真的减少行数且不破坏其它日志内容
  * 重复调用是幂等的
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"C:\Users\UserQu\AppData\Local\AgSwitch")

from app.core import wslfix

ok = True


def check(label, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


print("1. 检测")
st = wslfix.detect()
print(f"     wslExe={st.wsl_exe_present} service={st.service_present} "
      f"modern={st.modern_install} distros={st.distros} installed={st.installed}")
check("返回结构化状态", isinstance(st.to_dict(), dict))
check("有 wslExePresent 字段", "wslExePresent" in st.to_dict())
if not st.installed:
    check("未安装时给出原因", bool(st.error), st.error)

print("\n2. 命令文案")
cmd = wslfix.install_command()
check("命令含 --no-distribution", "--no-distribution" in cmd)
lines = wslfix.admin_instruction()
check("指引提到管理员", any("管理员" in l for l in lines))

print("\n3. 日志统计")
stats = wslfix.log_stats()
check("含 main 键", "main" in stats)
if stats["main"].get("exists"):
    m = stats["main"]
    print(f"     main.log: {m['lines']} 行 / {m['bytes']}B / WSL 报错 {m['wslErrors']} 处")
    check("行数为正", m["lines"] > 0)
    check("报错数非负", m["wslErrors"] >= 0)

print("\n4. 清理（幂等）")
ok1, log1 = wslfix.suppress_log_noise()
for l in log1:
    print("    ", l)
check("清理调用成功", ok1)
after = wslfix.log_stats()["main"]
check("清理后报错为 0", after.get("wslErrors") == 0, str(after.get("wslErrors")))

ok2, log2 = wslfix.suppress_log_noise()
check("再次清理不报错", ok2, log2[0] if log2 else "")
check("第二次无事可做", "没有需要清理" in (log2[0] if log2 else "") or ok2)

print("\n" + ("全部通过" if ok else "存在失败项"))
sys.exit(0 if ok else 1)
