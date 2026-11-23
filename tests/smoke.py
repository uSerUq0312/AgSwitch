import json, sys, time, urllib.error, urllib.request

sys.path.insert(0, r"C:\Users\UserQu\AppData\Local\AgSwitch")

from app.core import api as api_mod, identity, oauth, procs, wincred
from app.core.store import Store

ok_all = True


def check(label, cond, extra=""):
    global ok_all
    mark = "PASS" if cond else "FAIL"
    if not cond:
        ok_all = False
    print(f"  [{mark}] {label}" + (f"  {extra}" if extra else ""))


print("=" * 72)
print("1. 身份读取")
cur = identity.read_current()
check("读到 gemini:antigravity 凭据", cur is not None)
if cur:
    print(f"       email={cur.email or '(none)'}  auth={cur.auth_method}  expiry={identity.fmt_time(cur.expiry)}")
    check("access_token 非空", bool(cur.token.get("access_token")))
    check("refresh_token 非空", bool(cur.token.get("refresh_token")))
    check("邮箱标记已同步", cur.email == (cur.account_flags.get("agy.onboarding.lastLoginUsername") or ""))

print("=" * 72)
print("2. 凭据写回幂等")
raw_before = wincred.read_raw(identity.CRED_TARGET)
identity.apply_identity(cur, sync_flags=False)
raw_after = wincred.read_raw(identity.CRED_TARGET)
b1, b2 = json.loads(raw_before), json.loads(raw_after)
check("refresh_token 未变", b1["token"]["refresh_token"] == b2["token"]["refresh_token"])
check("auth_method 保留", b1.get("auth_method") == b2.get("auth_method"))
check("writer_id 保留", b1.get("writer_id") == b2.get("writer_id"))
check("长度一致", len(raw_before) == len(raw_after), f"{len(raw_before)} vs {len(raw_after)}")

print("=" * 72)
print("3. 账号库（DPAPI）")
s = Store()
saved, is_new = s.upsert(cur)
print(f"       upsert -> {saved.display} (new={is_new})")
s2 = Store()
check("重读账号数一致", len(s2.accounts) == len(s.accounts))
check("指纹一致", s2.accounts[0].fingerprint == saved.fingerprint)
check("文件为 DPAPI 加密", s2.path.read_bytes()[:6] == b"AGSW1\x00")
try:
    from app.core.store import dpapi_decrypt
    blob = s2.path.read_bytes()[6:]
    plain = dpapi_decrypt(blob)
    check("可解密回 JSON", json.loads(plain).get("accounts") is not None)
except Exception as exc:  # noqa: BLE001
    check("可解密回 JSON", False, str(exc))

print("=" * 72)
print("4. 进程探测")
hub = procs.hub_proc()
procs_list = procs.list_procs()
print(f"       hub={hub.pid if hub else None}  procs={[(p.pid, p.name) for p in procs_list]}")
check("list_procs 不误伤 t3code", all("t3" not in p.lname for p in procs_list))
check("hub_endpoint 可调用", True, str(procs.hub_endpoint()))

print("=" * 72)
print("5. token 有效期判定")
if cur:
    alive = oauth.token_alive(cur.token)
    check("token_alive 可计算", isinstance(alive, bool), f"alive={alive}")
    try:
        oauth.try_refresh(cur.token["refresh_token"], "")
        check("空 secret 被拒", False)
    except oauth.RefreshError:
        check("空 secret 被拒", True)

print("=" * 72)
print("6. HTTP API")
srv, api = api_mod.start_background()
port = srv.server_address[1]
tok = api.token


def call(path, body=None, with_token=True):
    headers = {"Content-Type": "application/json"}
    if with_token:
        headers["X-AgSwitch-Token"] = tok
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api{path}",
        None if body is None else json.dumps(body).encode(),
        headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


st, data = call("/state")
check("/state 200", st == 200 and data.get("ok"))
print(f"       hub={data['status']['hubRunning']} accounts={len(data['accounts'])} cred={data['status']['credPresent']}")

st, _ = call("/state", with_token=False)
check("无 token 被拒 401", st == 401)

st, diag = call("/diagnose", {})
check("/diagnose 200", st == 200 and diag.get("ok"))
if diag.get("log"):
    print("       " + diag["log"][0])

st, cap = call("/capture", {})
check("/capture 200", st == 200 and cap.get("ok"), cap.get("message", ""))

fp = api.svc.store.accounts[0].fingerprint
st, rn = call("/rename", {"fp": fp, "label": "主号"})
check("/rename 200", st == 200 and rn.get("ok"))

st, bad = call("/switch", {"fp": "nonexistent", "restart": False})
check("未知账号返回 400", st == 400 and not bad.get("ok"))

# 只写凭据 + 不重启，验证切换链路里非进程部分
st, sw = call("/switch", {"fp": fp, "restart": False, "clearSession": False})
check("/switch(不重启) 200", st == 200 and sw.get("ok"), str(sw.get("log")))

for p in ("/", "/static/app.css", "/static/app.js"):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{p}", timeout=10) as r:
        body = r.read()
    check(f"静态 {p} 200", r.status == 200 and len(body) > 100, f"{len(body)}B")

with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
    html = r.read().decode()
for needle in ("AgSwitch", 'id="list"', 'id="btn-capture"', "底层机制"):
    check(f"HTML 含 {needle}", needle in html)

srv.shutdown()

print("=" * 72)
print("全部通过" if ok_all else "存在失败项")
sys.exit(0 if ok_all else 1)
