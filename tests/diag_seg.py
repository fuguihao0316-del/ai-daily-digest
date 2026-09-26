# -*- coding: utf-8 -*-
"""Historical diagnostic (2026-09-26): split the glued papers section on every
inline `- **` and report, per segment, whether it parses as an item and whether
its title is over MAX_TITLE_CHARS.

Pinned to the rev that produced the glued artifact. The live file was replaced
by the 2026-09-26 re-run (b3f4a7a2), whose papers section parses cleanly, so a
working-tree read now describes a document that never had the bug. To diagnose a
*new* problem, rewrite this against the current rev instead of unpinning.
"""
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "scripts")
import summarize as S  # noqa: E402

FIXTURE_REV = "f246ecf6"
md = subprocess.run(["git", "show", f"{FIXTURE_REV}:daily/2026-09-26.md"],
                    capture_output=True, check=True).stdout.decode("utf-8")
sec = md.split("## 📄 学术论文研究动态", 1)[1].split("### 今日观察", 1)[0]
glue = re.compile(r"(?=-\s*\*\*)")

for ln in sec.splitlines():
    pieces = [x for x in glue.split(ln) if x.strip()]
    if len(pieces) < 2:
        continue
    print("LINE starts:", ln[:34])
    for n, s in enumerate(pieces):
        m = S._ITEM_RE.match(s)
        if not m:
            print("   seg%d NO ITEM MATCH: %r" % (n, s[:80]))
            continue
        t = m.group(1).strip().strip("[]").strip()
        flag = "TOO LONG" if len(t) > S.MAX_TITLE_CHARS else "ok"
        print("   seg%d title %3d chars [%s]  %s" % (n, len(t), flag, t[:78]))
    print()
