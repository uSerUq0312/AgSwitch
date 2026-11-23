import json, sys, urllib.error, urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 53379
TOK = sys.argv[2] if len(sys.argv) > 2 else ""


def get(path):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers={"X-AgSwitch-Token": TOK})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def post(path, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        json.dumps(body).encode(),
        {"X-AgSwitch-Token": TOK, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


ok = True


def check(label, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


st, state = get("/api/state")
check("/api/state", st == 200 and state.get("ok"))
s = state["status"]
print(f"       hub={s['hubRunning']} lsOnline={s['lsOnline']} port={s['hubPort']} cred={s['credPresent']}")
check("LS 已识别为在线", s["lsOnline"])

st, q = get("/api/quota?force=1")
check("/api/quota 200", st == 200 and q.get("ok"), q.get("message", ""))
if q.get("ok"):
    d = q["data"]
    acc = d["account"]
    grp = d["quota"]["groups"]
    print(f"       账号 {acc['email']} · {acc['plan']} · credits {acc['promptCreditsLeft']}/{acc['promptCreditsTotal']}")
    for g in grp:
        for b in g["buckets"]:
            print(f"       {g['name'][:22]:24s} {b['name'][:26]:28s} {b['percent']:3d}%  {b['resetAt']}")
    check("解析出模型分组", len(grp) >= 2, f"{len(grp)} 组")
    check("账号邮箱非空", bool(acc["email"]))
    check("计划名非空", bool(acc["plan"]))
    check("有额度桶", sum(len(g["buckets"]) for g in grp) >= 3)

st, lg = post("/api/login", {})
check("/api/login 200", st == 200 and lg.get("ok"), lg.get("message", ""))
if lg.get("data"):
    print(f"       alreadySignedIn={lg['data'].get('alreadySignedIn')} authUrl={lg['data'].get('authUrl') or '(无)'}")

# 静态资源
for p in ("/", "/static/app.css", "/static/app.js"):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{p}", timeout=10) as r:
        n = len(r.read())
    check(f"静态 {p}", r.status == 200 and n > 100, f"{n}B")

with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=10) as r:
    html = r.read().decode()
for needle in ("id=\"quota-body\"", "刷新额度", "btn-glogin", "RetrieveUserQuotaSummary"):
    check(f"HTML 含 {needle}", needle in html)

print("\n" + ("全部通过" if ok else "存在失败项"))
sys.exit(0 if ok else 1)
