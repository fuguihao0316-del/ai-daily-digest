# -*- coding: utf-8 -*-
"""Acceptance data for the first cloud run carrying the gluing fix (3bfdf9de).

Answers the five checks, in order, straight out of the committed artifacts:
  1. papers item count (expect 15 = 10 positive + 5 alternate)
  2. any remaining gluing (raw.json split warnings + inline `- **` in the md)
  3. fate of the split-out items (papers: hard fail / industry: degrade)
  4. [observation] overshoot (cap is 350; overshoot is by design, but record N)
  5. source-resolution failure rate vs RESOLVE_FAIL_LIMIT = 30%

Usage: python verify_run.py [DATE] [REV]      (defaults 2026-09-27, origin/main)
"""
import json
import re
import statistics
import subprocess
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "scripts")
from generate_site import parse_digest  # noqa: E402

DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-09-27"
# REV is required on purpose. `origin/main` is a MOVING ref, and a verification
# tool that silently follows it will one day verify the wrong document and say
# everything is fine. Name the revision you actually mean.
if len(sys.argv) < 3:
    raise SystemExit("用法: python verify_run.py <DATE> <REV>\n"
                     "  例: python verify_run.py 2026-09-27 origin/main")
REV = sys.argv[2]

GLUED = re.compile(r"-\s*\*\*")
SPLIT_WARN = re.compile(r"模型把 (\d+) 条写在了同一行，已按行内 - \*\* 拆分")
PAPERS_SPLIT_FAIL = "从粘连行拆出，标题无法在论文候选池中精确匹配"
INDUSTRY_SPLIT_DEGRADE = "从粘连行拆出且来源无法验证"
RESOLVE_FAIL = "来源无法在候选池中定位，已删除来源行"
OBS_WARN = re.compile(r"\[observation\] (\d+) 字，超出 (\d+) 字上限")


def show(path):
    """Read a path straight out of a revision; None when it does not exist."""
    out = subprocess.run(["git", "show", f"{REV}:{path}"], capture_output=True)
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8")


def head(tag):
    print()
    print("=" * 72)
    print(tag)
    print("=" * 72)


raw_text = show(f"data/{DATE}.raw.json")
md = show(f"daily/{DATE}.md")

if md is None and raw_text is None:
    print(f"!! {REV} 没有 {DATE} 的任何产物——这一天没有发布。")
    print("   CI 变红时不会留下 daily/ 和 data/，也就没有可核对的正文。")
    sys.exit(2)

raw = json.loads(raw_text) if raw_text else {}
warns = raw.get("warnings") or []
digest = parse_digest(md) if md else {"categories": [], "observation": ""}
cats = [c for c in digest["categories"] if c["items"] or c["alternates"]]

print(f"日期 {DATE}   版本 {REV}")
print(f"generated_at: {raw.get('generated_at')}")
print(f"告警总数: {len(warns)}")

# ── 1. item counts ────────────────────────────────────────────────────────────
head("1. 条目数")
for cat in cats:
    pos, alt = len(cat["items"]), len(cat["alternates"])
    total = pos + alt
    flag = "" if total == 15 else ("   <-- 未满 15" if total >= 10 else "   <-- 低于软下限 10")
    print(f"  [{cat['name']}] 正选 {pos} + 备选 {alt} = {total}{flag}")
print("\n  注：软下限 10，满额 15。低于 15 但 >=10 属正常发布。")

# ── 2. gluing ─────────────────────────────────────────────────────────────────
head("2. 是否还有粘连")
split_warns = [w for w in warns if SPLIT_WARN.search(w)]
print(f"  raw.json 里的拆分告警: {len(split_warns)} 条")
for w in split_warns:
    print(f"    ! {w}")

inline = []
for cat in cats:
    for kind in ("items", "alternates"):
        for n, item in enumerate(cat[kind], 1):
            hits = len(GLUED.findall(item["desc"]))
            if hits:
                inline.append((cat["name"], kind[:3], n, hits, len(item["desc"]), item["title"]))
print(f"\n  daily/{DATE}.md 正文里残留的内联 `- **`: {len(inline)} 处")
if inline:
    print("  （残留 = 拆分没接住，这条正文里还裹着别的条目）")
    for name, kind, n, hits, ln, title in inline:
        print(f"    {name} {kind}{n}  粘 {hits} 条  正文 {ln} 字  {title[:40]}")
else:
    print("  正文干净：没有条目被裹进上一条的正文。")

if split_warns:
    print("\n  拆分出来的条目正文长度（拆前是一条超长正文，拆后应各自落在预算内）:")
    for cat in cats:
        lens = [len(i["desc"]) for i in cat["items"] + cat["alternates"]]
        print(f"    [{cat['name']}] min={min(lens)} 中位={int(statistics.median(lens))} "
              f"max={max(lens)} 均值={statistics.mean(lens):.0f}")

# ── 3. what happened to the split-out items ───────────────────────────────────
head("3. 拆出来的条目的下场")
papers_fail = [w for w in warns if PAPERS_SPLIT_FAIL in w]
ind_degrade = [w for w in warns if INDUSTRY_SPLIT_DEGRADE in w]
prefix = f"[{DATE}] "

print(f"  papers 拆分后无法精确匹配（硬失败，重试 3 次则当天不发）: {len(papers_fail)} 条")
for w in papers_fail:
    print(f"    !! {w}")
print(f"\n  industry 拆分后来源无法验证、已降级删来源行（不失败，只降级）: {len(ind_degrade)} 条")
for w in ind_degrade:
    print(f"    ! {w}")

for w in warns:
    if "无法拆分成合法条目" in w:
        print(f"\n  拆分中途放弃、整行保留为单条: {w}")

print("\n  判读：")
if papers_fail:
    print("    papers 有硬失败告警——若这条日期仍有产物，说明是重试后成功的。")
elif split_warns:
    print("    papers 拆分出的条目全部在候选池里精确命中（拆分救得回来）。")
else:
    print("    本日没有发生拆分，第 3 项不适用。")

if ind_degrade:
    print("    industry 出现降级——请累计连续天数，连续 3 天要重审提示词契约。")
elif split_warns:
    print("    industry 未出现降级。")
else:
    print("    industry 未粘连（与 09-26 一致）。")

# ── 4. observation ────────────────────────────────────────────────────────────
head("4. [observation] 超限告警")
obs = digest.get("observation") or ""
obs_w = [w for w in warns if OBS_WARN.search(w)] + [o for o in [raw.get("observation_warning")] if o]
print(f"  正文里的今日观察长度: {len(obs)} 字")
measured = None
for w in obs_w:
    for m in OBS_WARN.finditer(w):
        measured = int(m.group(1))
        print(f"  告警: {w}")
if measured is None and obs:
    print("  没有超限告警。（超限是设计如此，不是 bug；没触发就是这次没超。）")
if measured is not None:
    n = measured
    verdict = ("持续 380+ —— 契约需要重切，标记进记忆重评估"
               if n >= 380 else "低于 380，属观察范围内")
    print(f"  记下 N = {n}：{verdict}")

# ── 5. source resolution rate ─────────────────────────────────────────────────
head("5. 来源定位失败率（RESOLVE_FAIL_LIMIT = 30%）")
resolve_fail = {}
for w in warns:
    m = re.match(r"\[(\w+)\] .*" + re.escape(RESOLVE_FAIL), w)
    if m:
        resolve_fail.setdefault(m.group(1), []).append(w)

for cat in cats:
    key = "papers" if "论文" in cat["name"] else "industry"
    pos, alt = cat["items"], cat["alternates"]
    total = len(pos) + len(alt)
    nosrc = [i for i in pos + alt if not i["sources"]]
    unresolved = len(resolve_fail.get(key, []))
    rate = unresolved / total if total else 0
    print(f"  [{key}] {unresolved}/{total} 条计入失败率 = {rate:.0%}"
          f"  （阈值 30%，{'未达' if rate < 0.30 else '已达/超过'}）")
    print(f"    文档里没有来源行的条目: {len(nosrc)} 条"
          f"（含拆分降级，那类不计入失败率）")

if any(resolve_fail.values()):
    print("\n  逐条:")
    for key, ws in resolve_fail.items():
        for w in ws:
            print(f"    ! {w}")
else:
    print("\n  没有任何条目因定位失败被删来源行。")

print("""
  判读：这条门限把整批重试，重试 3 次仍超则当天不发（CI 变红）。
  产物存在 = 最终那次尝试低于阈值。中途某次是否触发过重试，
  只会出现在 Actions 日志里（需登录），产物里不留痕。""")
print("=" * 72)
