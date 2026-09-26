# -*- coding: utf-8 -*-
"""Re-split the glued bodies to recover the true length distribution."""
import re
import statistics
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "scripts")
from generate_site import parse_digest  # noqa: E402

# `origin/main` is a MOVING ref and must not be used as a fixture here: the
# 2026-09-26 re-run (b3f4a7a2) replaced the glued artifact with a clean one, so
# pointing at origin/main now reports "no gluing" for a document that was full
# of it. Pin the rev that actually produced the glued output.
# Usage: python split_glued.py [REV] [DATE]
REV = sys.argv[1] if len(sys.argv) > 1 else "f246ecf6"
DATE = sys.argv[2] if len(sys.argv) > 2 else "2026-09-26"

md = subprocess.run(["git", "show", f"{REV}:daily/{DATE}.md"],
                    capture_output=True).stdout.decode("utf-8")
digest = parse_digest(md)

for cat in digest["categories"]:
    if not (cat["items"] or cat["alternates"]):
        continue
    print("=" * 72)
    print(cat["name"])
    print("=" * 72)
    for kind in ("items", "alternates"):
        raw = [len(i["desc"]) for i in cat[kind]]
        # body -> [lead, glued, glued...]; the lead keeps the item's own metadata
        split = []
        for item in cat[kind]:
            segs = re.split(r"-\s*\*\*", item["desc"])
            split += [len(segs[0])] + [len(s) for s in segs[1:]]
        if len(split) != len(raw):
            print(f"\n{kind}: {len(raw)} 条 -> 拆开后 {len(split)} 条")
            print(f"  实际写出的正文: min={min(split)} 中位={int(statistics.median(split))} "
                  f"max={max(split)} 均值={statistics.mean(split):.0f}")
            print(f"    " + " ".join(str(n) for n in split))
        else:
            print(f"\n{kind}: {len(raw)} 条（无粘连）: min={min(raw)} "
                  f"中位={int(statistics.median(raw))} max={max(raw)} "
                  f"均值={statistics.mean(raw):.0f}")
