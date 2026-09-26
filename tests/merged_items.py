# -*- coding: utf-8 -*-
"""Count items the model wrote but the parser never saw, because it glued
several `- **标题**：正文` items onto one physical line.

_split_items only starts a new item on a line that *begins* with `- **`, so an
item glued to the tail of the previous body is swallowed into that body. That
is invisible to every existing guard (the body is long, not short), so it ships.
"""
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "scripts")
from generate_site import parse_digest  # noqa: E402

# Any `- **` inside a body is a glued item: a legitimate body never contains a
# dash-plus-bold (prompt rule 4 forbids Markdown in the body).
GLUED = re.compile(r"-\s*\*\*")

# Revisions are explicit, never `origin/main`: that ref is MOVING, and the
# 2026-09-26 re-run (b3f4a7a2) replaced the glued artifact with a clean one —
# defaulting to it would compare the wrong document and report "0 swallowed".
# Usage: python merged_items.py [DATE] [REV_BEFORE] [REV_AFTER]
DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-09-26"
REVS = tuple(sys.argv[2:]) or ("4bda296c", "f246ecf6")

for rev in REVS:
    md = subprocess.run(["git", "show", f"{rev}:daily/{DATE}.md"],
                        capture_output=True).stdout.decode("utf-8")
    digest = parse_digest(md)
    print("=" * 72)
    print(rev)
    print("=" * 72)
    for cat in digest["categories"]:
        if not (cat["items"] or cat["alternates"]):
            continue
        parsed = len(cat["items"]) + len(cat["alternates"])
        written = 0
        print(f"\n[{cat['name']}]")
        for kind in ("items", "alternates"):
            for n, item in enumerate(cat[kind], 1):
                body = item["desc"]
                glued = len(GLUED.findall(body))
                written += 1 + glued
                flag = f"   <-- 粘了 {glued} 条（正文被撑到 {len(body)} 字）" if glued else ""
                print(f"  {kind[:3]}{n:>2} {len(body):>5} 字  {item['title'][:46]}{flag}")
        print(f"  ==> 解析出 {parsed} 条，模型实际写了 {written} 条，被吞 {written - parsed} 条")
