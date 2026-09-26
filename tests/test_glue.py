# -*- coding: utf-8 -*-
"""Verify the glued-line split against the REAL 2026-09-26 output.

The shipped digest is in the model's own format (minus the 备选 heading we
insert), so the papers section can be fed straight back into _split_items. On
2026-09-26 that section held 4 items glued onto the tails of other items' lines
and shipped as 11. If the split works, it must recover 15 — and the 4 recovered
papers must resolve against the real candidate pool by title, which is what
gives them a 来源 line back.
"""
import json
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")
sys.modules.setdefault("feedparser", types.ModuleType("feedparser"))
sys.path.insert(0, "scripts")

import summarize as S  # noqa: E402
from sources import select_candidates  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print(f"  !! {msg}")
    else:
        print(f"  ok   {msg}")


# Pinned to the revision that produced the glued output. The working-tree copy
# was overwritten by the 2026-09-26 re-run (b3f4a7a2), whose papers section
# parsed cleanly — reading it here would have quietly turned checks [2]-[7]
# vacuous while [1] still passed, which is the worst way for a regression to die.
FIXTURE_REV = "f246ecf6"


def fixture(path):
    out = subprocess.run(["git", "show", f"{FIXTURE_REV}:{path}"], capture_output=True)
    if out.returncode != 0:
        raise SystemExit(f"fixture rev {FIXTURE_REV} has no {path}")
    return out.stdout.decode("utf-8")


md = fixture("daily/2026-09-26.md")

# Rebuild the papers answer as the model wrote it: drop the heading we insert.
section = md.split("## 📄 学术论文研究动态", 1)[1].split("### 今日观察", 1)[0]
model_answer = section.replace(S.ALTERNATES_HEADING, "").strip()

data = json.loads(fixture("data/2026-09-26.raw.json"))["items"]
pool = select_candidates(
    data,
    now=datetime.fromisoformat("2026-09-26").replace(tzinfo=timezone.utc) + timedelta(days=1),
)
papers_pool = pool["papers"]

print("[1] split the real glued answer")
items, warnings = S._split_items(model_answer)
print(f"     warnings: {[w for w in warnings if '拆分' in w]}")
check(len(items) == 15, f"recovered {len(items)} items, want 15 (11 shipped on 09-26)")

recovered = [i for i in items if i["split_out"]]
check(len(recovered) == 4, f"{len(recovered)} split-out items, want 4")

print("\n[2] no body is left over-long (the gluing fingerprint)")
longest = max(len(i["body"]) for i in items)
check(longest <= S.LONG_BODY, f"longest recovered body is {longest}, want <= {S.LONG_BODY}")

print("\n[3] resolve against the real pool")
warns, unresolved = S._finalize(items, papers_pool)
for item in recovered:
    check(item["resolved_by"] == "title",
          f"recovered by title: {item['title'][:44]} -> {item['resolved_by']}")
    check(item["source_line"].startswith("- 来源："),
          f"source line restored: {item['title'][:40]}")
check(unresolved == 0, f"{unresolved} normal items unresolved, want 0")

print("\n[4] verification passes for papers")
problems, warns2 = S._verify_split_items(items, "papers")
check(problems == [], f"papers verification raised problems: {problems}")

print("\n[5] a recovered paper with an unknown title must be a problem")
bogus = [dict(i) for i in recovered]
bogus[0]["resolved"] = False          # what a bad split looks like
bogus[0]["title"] = "A Paper That Is Not In The Pool"
bogus[0]["source_line"] = ""
probs, _ = S._verify_split_items(bogus, "papers")
check(len(probs) == 1 and "无法在论文候选池中精确匹配" in probs[0],
      f"unknown title did not become a problem: {probs}")

print("\n[6] industry recovered items degrade, never fail")
ind = [{"title": "某条中文改写的行业标题", "body": "正文", "stars": "", "source_line": "",
        "deprecated": False, "split_out": True, "resolved": False, "resolved_by": None}]
probs, warns3 = S._verify_split_items(ind, "industry")
check(probs == [], f"industry verification raised: {probs}")
check(len(warns3) == 1 and "降级删除来源行" in warns3[0], f"no degrade warning: {warns3}")

print("\n[7] gluing must not trip the 30% graded gate")
# 15 items where the 4 recovered ones are all unresolved: that is 27% of the
# class, close enough to the limit that counting them would make gluing
# (rather than a bad URL habit) decide whether the day ships.
S._finalize(items, papers_pool)
recovered_unresolved = sum(1 for i in recovered if not i["resolved"])
check(recovered_unresolved == 0,
      f"{recovered_unresolved} recovered items counted as unresolved")
_, unresolved_from_pool_of_one = S._finalize(
    [{"title": "不在池中", "body": "x", "stars": "", "source_line": "",
      "deprecated": False, "split_out": True}], [])
check(unresolved_from_pool_of_one == 0,
      "a split-out item with no pool match incremented the graded counter")

print("\n[8] inline bold (no leading dash) must NOT split")
inline = "- **标题**：正文含 **加粗** 字样，不应拆分\n  - 重要性：★★★★☆ / 5\n  - 来源：[X](https://x.com/a)"
check(len(S._split_items(inline)[0]) == 1, "inline bold split an item")

print("\n[9] a malformed glued segment aborts the whole split")
# Second segment has no closing `**`, so it is not a parseable item. The line
# must then be left completely alone: a suspiciously long body is a warning,
# whereas amputating a good body on a bad guess is silent damage.
broken = "- **甲**：正文甲。- **乙：正文乙没有收尾的星号\n  - 重要性：★★★★☆ / 5"
items_b, warns_b = S._split_items(broken)
check(len(items_b) == 1, f"malformed glue gave {len(items_b)} items, want 1 (no split)")
check(any("无法拆分" in w for w in warns_b), f"no abort warning: {warns_b}")
check("正文乙" in items_b[0]["body"], "the body was amputated instead of left alone")
check(items_b[0]["title"] == "甲", f"first title changed: {items_b[0]['title']}")

print("\n" + "=" * 60)
if failures:
    print(f"FAILED ({len(failures)}):")
    for f in failures:
        print("  -", f.splitlines()[0])
    sys.exit(1)
print("ALL GLUE CHECKS PASSED")
