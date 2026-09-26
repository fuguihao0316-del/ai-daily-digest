# -*- coding: utf-8 -*-
"""One-off (2026-09-26): verify a specific commit message survived PowerShell/git
intact — replacement chars, byte length, and the probes below.

NOT part of the green suite. The probes are literal substrings of commit
4bda296c's message, so run against any other commit this reports MANGLED and
exits 1 — by design, not a regression. Worth re-running (with fresh probes) any
time a Chinese commit message is passed through PowerShell."""
import subprocess

msg = subprocess.run(
    ["git", "log", "-1", "--format=%B"], capture_output=True
).stdout.decode("utf-8")

print("first line:", msg.splitlines()[0])
print("bytes:", len(msg.encode("utf-8")))
print("replacement chars:", msg.count("�"))

probes = [
    "论文池加深到 35 条",
    "≥10 即发布",
    "PAPER_CANDIDATES = 35",
    "条数不足是最严重的错误",
    "还差 M 条",
    "actual_positives",
    "MIN_AVG_BODY = 250",
]
missing = 0
for probe in probes:
    ok = probe in msg
    missing += 0 if ok else 1
    print(("FOUND  " if ok else "MISSING"), probe)

print("RESULT:", "OK" if not missing else "MANGLED")
raise SystemExit(1 if missing else 0)
