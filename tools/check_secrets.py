"""提交前检查：确认待提交内容里没有凭据泄漏。

扫描 git 暂存区里的所有文本文件，查找 Google OAuth token / client secret /
csrf token / JWT 等形态的字符串。
"""
from __future__ import annotations

import re
import subprocess
import sys

PATTERNS = {
    "access_token(ya29)": re.compile(r"ya29\.[A-Za-z0-9_\-]{15,}"),
    "refresh_token(1//0)": re.compile(r"1//0[A-Za-z0-9_\-]{15,}"),
    "client_secret": re.compile(r"GOCSPX-[A-Za-z0-9_\-]{8,}"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\."),
    "openai_rt": re.compile(r"rt\.1\.[A-Za-z0-9_\-]{10,}"),
    "csrf_uuid": re.compile(r'csrfToken"?\s*[:=]\s*"[0-9a-f]{8}-[0-9a-f]{4}'),
    "id_token": re.compile(r'"id_token"\s*:\s*"eyJ'),
}

BINARY_EXT = (".png", ".jpg", ".ico", ".exe", ".dat", ".zip", ".gz", ".pak", ".dll")


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], capture_output=True).stdout


def main() -> int:
    raw = git("diff", "--cached", "--name-only")
    files = [f for f in raw.decode("utf-8", errors="replace").splitlines() if f.strip()]
    print(f"待提交文件数：{len(files)}")

    findings: list[tuple[str, str, str]] = []
    for rel in files:
        if rel.lower().endswith(BINARY_EXT):
            continue
        blob = git("show", f":{rel}")
        if not blob:
            continue
        text = blob.decode("utf-8", errors="replace")
        for name, pat in PATTERNS.items():
            for m in pat.finditer(text):
                findings.append((rel, name, m.group()[:36]))

    if findings:
        print(f"\n[!] 发现 {len(findings)} 处疑似凭据：")
        for rel, name, sample in findings[:30]:
            print(f"    {rel}  [{name}]  {sample}")
        return 1

    print("\n[ok] 未发现凭据泄漏")
    print("\n提交清单：")
    for rel in files:
        print("   ", rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
