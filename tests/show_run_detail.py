# -*- coding: utf-8 -*-
"""Dump the new run's warnings + the alternates sections, read from git (not the
working tree, which still holds the pre-run files until we pull)."""
import json
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

# REV required: see verify_run.py — a moving `origin/main` default is how a
# diagnostics tool ends up confidently describing a document nobody asked about.
if len(sys.argv) < 2:
    raise SystemExit("用法: python show_run_detail.py <REV> [DATE]")
REV = sys.argv[1]
DATE = sys.argv[2] if len(sys.argv) > 2 else "2026-09-26"


def show(path):
    out = subprocess.run(["git", "show", f"{REV}:{path}"], capture_output=True)
    return out.stdout.decode("utf-8") if out.returncode == 0 else None


raw = json.loads(show(f"data/{DATE}.raw.json"))
warns = raw.get("warnings") or []
print(f"=== {REV} warnings ({len(warns)}) ===")
for n, w in enumerate(warns, 1):
    print(f"{n:>2}. {w}")

print(f"\n=== selection ===")
print(f"pool: {raw.get('pool')}")
sel = raw.get("selection") or {}
print(f"候选池: " + ", ".join(f"{k}={len(v)}" for k, v in sel.items()))

md = show(f"daily/{DATE}.md")
lines = md.splitlines()
print(f"\n=== {DATE}.md 结构 ===")
for i, l in enumerate(lines):
    if l.startswith("#"):
        print(f"  {i:>4}  {l}")

print("\n=== 备选段前 10 行原文 ===")
for i, l in enumerate(lines):
    if l.strip().startswith("###") and "备选" in l:
        for l2 in lines[i:i + 12]:
            print("  " + repr(l2[:120]))
        print("  ---")
