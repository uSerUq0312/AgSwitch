import sys, json
sys.path.insert(0, r"C:\Users\UserQu\AppData\Local\AgSwitch")
from app.core import lsapi

ep = lsapi.find_endpoint()
print("endpoint:", ep)
if not ep:
    sys.exit(1)

print("healthz:", lsapi.probe(ep.http_port))

acc = lsapi.fetch_account(ep)
print("\n=== 账号 ===")
print(json.dumps(acc.to_dict(), ensure_ascii=False, indent=2))

q = lsapi.fetch_quota(ep)
print("\n=== 额度 ===")
for g in q.groups:
    print(f"\n[{g.name}]")
    for b in g.buckets:
        flag = " (不适用)" if b.disabled else ""
        print(f"   {b.name:32s} {b.percent:3d}%  重置 {b.reset_at}{flag}")
t = q.tightest
print("\n最紧张:", t.name if t else None, t.percent if t else None)
