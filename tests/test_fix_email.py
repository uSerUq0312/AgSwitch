import json, sys, urllib.error, urllib.request

PORT = int(sys.argv[1])
TOK = sys.argv[2]


def post(path, body=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        json.dumps(body or {}).encode(),
        {"X-AgSwitch-Token": TOK, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def get(path):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers={"X-AgSwitch-Token": TOK})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode())


ok = True


def check(label, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


st = get("/api/state")
s = st["status"]
print(f"  初始：live={s['liveEmail']} flagged={s['flaggedEmail']} mismatch={s['emailMismatch']}")

# 自己制造一次错位（模拟"凭据换了号，标记没跟上"），再验证能检测并能修
import sys
sys.path.insert(0, r"C:\Users\UserQu\AppData\Local\AgSwitch")
from app.core import identity

dirs = identity.state_dirs()
if not dirs or not s["liveEmail"]:
    print("  [SKIP] 没有状态目录或没有 live 身份，跳过")
    sys.exit(0)

storage = dirs[0] / identity.APP_STORAGE
data = identity.read_json(storage)
real = s["liveEmail"]
data["agy.onboarding.lastLoginUsername"] = "stale-marker@example.com"
identity.write_text_atomic(storage, json.dumps(data, ensure_ascii=False, indent=2))
print(f"  已人为把标记改成 stale-marker@example.com（真实身份 {real}）")

st = get("/api/state")
s = st["status"]
print(f"  制造后：live={s['liveEmail']} flagged={s['flaggedEmail']} mismatch={s['emailMismatch']}")
check("检测到邮箱错位", s["emailMismatch"] is True)

st, fix = post("/api/fix-email")
check("同步标记 200", st == 200 and fix.get("ok"), fix.get("message", ""))
for line in fix.get("log") or []:
    print("       ", line)

st = get("/api/state")
s = st["status"]
print(f"  修复后：live={s['liveEmail']} flagged={s['flaggedEmail']} mismatch={s['emailMismatch']}")
check("错位已消除", s["emailMismatch"] is False)
check("标记已对齐真实身份", s["flaggedEmail"].lower() == s["liveEmail"].lower())

# 账号库 should show live credential flag
accs = st["accounts"]
print(f"  账号库 {len(accs)} 条")
for a in accs:
    print(f"       {a['label'][:20]:22s} active={a['active']} live={a['isLiveCredential']} {a['email']}")

print("\n" + ("全部通过" if ok else "存在失败项"))
sys.exit(0 if ok else 1)
