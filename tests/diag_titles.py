# -*- coding: utf-8 -*-
"""Historical diagnostic (2026-09-26): how long are real candidate titles?

MAX_TITLE_CHARS is the line-start guard: a line starting with `- **` whose title
runs past it is treated as a body continuation, not an item — so a real title
above the limit is silently folded into the previous item's body. This measured
the pool maxima that set the limit.

Both fixtures are pinned to one rev. `data/*.raw.json` is live pipeline output
that any re-run overwrites, and `daily/2026-09-26.md` was already replaced by
the 2026-09-26 re-run (b3f4a7a2) — so a working-tree read measures a different
document every time the pipeline touches that date. To diagnose a *new* problem,
rewrite this against the current rev instead of unpinning.
"""
import json
import statistics
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")
sys.modules.setdefault("feedparser", types.ModuleType("feedparser"))
sys.path.insert(0, "scripts")
from sources import select_candidates  # noqa: E402
import summarize as S  # noqa: E402

FIXTURE_REV = "f246ecf6"


def fixture(path):
    out = subprocess.run(["git", "show", f"{FIXTURE_REV}:{path}"],
                         capture_output=True, check=True)
    return out.stdout.decode("utf-8")


print("MAX_TITLE_CHARS =", S.MAX_TITLE_CHARS)
print()

for date in ("2026-09-24", "2026-09-26"):
    data = json.loads(fixture(f"data/{date}.raw.json"))["items"]
    pool = select_candidates(
        data, now=datetime.fromisoformat(date).replace(tzinfo=timezone.utc) + timedelta(days=1))
    papers = [c["title"] for c in pool["papers"]]
    industry = [c["title"] for c in pool["industry"]["news"] + pool["industry"]["projects"]]
    print(f"=== {date} ===")
    for label, titles in (("papers", papers), ("industry", industry)):
        lens = sorted(len(t) for t in titles)
        over = [t for t in titles if len(t) > S.MAX_TITLE_CHARS]
        print(f"  {label:9s} n={len(titles):3d}  min={lens[0]:3d} median={int(statistics.median(lens)):3d} "
              f"max={lens[-1]:3d}  超 {S.MAX_TITLE_CHARS} 字的: {len(over)}")
        for t in sorted(over, key=len, reverse=True)[:4]:
            print(f"        {len(t):3d}  {t[:88]}")
    print()

# And what the model actually wrote on 09-26, recovered from the glued lines.
md = fixture("daily/2026-09-26.md")
sec = md.split("## 📄 学术论文研究动态", 1)[1].split("### 今日观察", 1)[0]
import re  # noqa: E402
titles = [m.strip().strip("[]").strip()
          for m in re.findall(r"-\s*\*\*(.+?)\*\*\s*[：:]", sec)]
print("=== 09-26 模型实际写出的论文标题（含被粘连吞掉的）===")
print(f"  n={len(titles)}  超 {S.MAX_TITLE_CHARS} 字的: "
      f"{sum(1 for t in titles if len(t) > S.MAX_TITLE_CHARS)}")
for t in titles:
    mark = "  <-- 超限" if len(t) > S.MAX_TITLE_CHARS else ""
    print(f"  {len(t):3d}  {t[:80]}{mark}")
