"""切换链路验证：不重启进程的前提下，验证"写凭据 → 标记 → 回读"闭环，并确保能还原。

用法：
    python tests/switch_roundtrip.py
会：
  1. 备份当前凭据与 app_storage；
  2. 把当前账号存为 A，再人工构造一个"B 账号"（token 换成假值，仅用于验证写回路径）；
  3. 切到 B，读回校验凭据确实变成 B；
  4. 切回 A，校验完全还原成原始 blob；
  5. 清理测试账号。
"""
from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, r"C:\Users\UserQu\AppData\Local\AgSwitch")

from app.core import identity, wincred
from app.core.store import Store


def main() -> int:
    ok = True

    def check(label, cond, extra=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))

    original = wincred.read_raw(identity.CRED_TARGET)
    if original is None:
        print("没有现存凭据，无法测试")
        return 1
    orig_blob = json.loads(original)
    orig_storage = {
        d: (d / identity.APP_STORAGE).read_bytes()
        for d in identity.state_dirs()
        if (d / identity.APP_STORAGE).exists()
    }

    store = Store()
    live = identity.read_current()
    # 老版本存下来的记录可能缺 id_token_email，这里补一次（等价于 UI 里点「存入账号库」）
    if not live.id_token_email:
        live.id_token_email = identity.effective_email(live)
    a, _ = store.upsert(live)
    fp_a = a.fingerprint

    # 构造 B：同一份 token，但 refresh_token 换掉 -> 指纹不同，模拟第二个账号
    fake = identity.Identity.from_dict(live.to_dict())
    fake.token = dict(live.token)
    fake.token["refresh_token"] = "1//TESTFIXTURE-" + "x" * 30
    fake.token["access_token"] = "ya29.TESTFIXTURE"
    fake.email = "fixture-b@example.com"
    fake.id_token_email = "fixture-b@example.com"
    fake.label = "测试B"
    fake.account_flags = {
        "agy.onboarding.lastLoginUsername": "fixture-b@example.com",
        "agy.onboarding.lastLoginIsGcpTos": "true",
    }
    b, _ = store.upsert(fake)
    fp_b = b.fingerprint
    print(f"  A={a.display} fp…{fp_a[-8:]}   B={b.display} fp…{fp_b[-8:]}")

    try:
        print("\n-- 切到 B")
        log = identity.apply_identity(b)
        print("     " + " | ".join(log))
        after_b = json.loads(wincred.read_raw(identity.CRED_TARGET))
        check("凭据 refresh_token 变成 B", after_b["token"]["refresh_token"] == fake.token["refresh_token"])
        check("凭据 auth_method 保留", after_b.get("auth_method") == orig_blob.get("auth_method"))
        cur_b = identity.read_current()
        check("回读邮箱 = B", cur_b.email == "fixture-b@example.com", cur_b.email)
        check(
            "identity 判定为 B（不是继承 A）",
            identity.effective_email(cur_b) == "fixture-b@example.com",
            identity.effective_email(cur_b),
        )
        check("B 与标记一致，无错位", identity.flag_mismatch(cur_b)[0] is False)

        print("\n-- 切回 A")
        log = identity.apply_identity(a)
        print("     " + " | ".join(log))
        after_a = json.loads(wincred.read_raw(identity.CRED_TARGET))
        check(
            "refresh_token 还原",
            after_a["token"]["refresh_token"] == orig_blob["token"]["refresh_token"],
        )
        check(
            "access_token 还原",
            after_a["token"]["access_token"] == orig_blob["token"]["access_token"],
        )
        check("邮箱标记还原", identity.read_current().email == (live.email or ""))

        # 回归：写回不能丢字段（曾经把 id_token_email 冲成空串）
        if "id_token_email" in orig_blob:
            after = json.loads(wincred.read_raw(identity.CRED_TARGET))
            check(
                "id_token_email 未被清空",
                after.get("id_token_email") == orig_blob.get("id_token_email"),
                f"{after.get('id_token_email')!r}",
            )
        for k in orig_blob:
            after = json.loads(wincred.read_raw(identity.CRED_TARGET))
            check(f"顶层字段 {k} 保留", k in after)
    finally:
        # 无论如何都还原原始凭据与 storage
        wincred.write_raw(
            identity.CRED_TARGET,
            original,
            username=identity.CRED_USER,
            comment="",
            persist=2,
        )
        for d, data in orig_storage.items():
            (d / identity.APP_STORAGE).write_bytes(data)
        store.remove(fp_b)
        print("\n  [done] 原始凭据与 app_storage 已还原，测试账号已移除")

    final = wincred.read_raw(identity.CRED_TARGET)
    check("原始 blob 字节级还原", final == original, f"{len(final)}B")
    print("\n结果：", "全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
