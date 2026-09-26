"""Stage 3 verification 1: deterministic pre-selection against real fixtures.

Offline: feedparser is not installed on this box, so it is stubbed before
importing sources (only the fetch_* functions need it).
"""
import json
import subprocess
import sys
import types
from collections import Counter
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding="utf-8")
sys.modules["feedparser"] = types.ModuleType("feedparser")
sys.path.insert(0, "scripts")

import sources  # noqa: E402
from sources import (select_candidates, base_source, MIN_SUMMARY_CHARS,  # noqa: E402
                     MAX_PER_SOURCE, PAPER_CANDIDATES)

# Pinned, not read from the working tree: `data/*.raw.json` is live pipeline
# output, so re-running any of these days would swap the fixture underneath the
# test and silently change what it asserts. Needs a full clone (not fetch-depth 1).
FIXTURE_REV = "f246ecf6"


def fixture(path):
    out = subprocess.run(["git", "show", f"{FIXTURE_REV}:{path}"],
                         capture_output=True, check=True)
    return out.stdout.decode("utf-8")


DAYS = ["2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


for day in DAYS:
    payload = json.loads(fixture(f"data/{day}.raw.json"))
    data = payload["items"]
    now = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + timedelta(days=1)

    pool = select_candidates(data, now=now)
    news, projects, papers = pool["industry"]["news"], pool["industry"]["projects"], pool["papers"]

    print(f"\n=== {day} ===")
    print(f"  raw: news={len(data['news'])} projects={len(data['projects'])} papers={len(data['papers'])}")
    print(f"  pool: news={len(news)} projects={len(projects)} papers={len(papers)}")

    # Papers get a deeper pool than industry (PAPER_CANDIDATES, decoupled from
    # CANDIDATES_PER_CLASS on purpose). HF_QUOTA + ARXIV_QUOTA == PAPER_CANDIDATES,
    # so nothing eligible is dropped — the pool is the whole supply up to the cap.
    # On days arXiv returns nothing (09-20/21/22) HF fills it alone.
    check(len(papers) <= PAPER_CANDIDATES,
          f"{day}: papers pool {len(papers)} > {PAPER_CANDIDATES}")
    check(len(papers) == min(PAPER_CANDIDATES, len(data["papers"])),
          f"{day}: papers pool {len(papers)} != min({PAPER_CANDIDATES}, {len(data['papers'])})")
    check(len(projects) == 5, f"{day}: projects pool {len(projects)} != 5")
    check(len(news) + len(projects) == 25, f"{day}: industry pool {len(news)+len(projects)} != 25")

    # per-source cap
    caps = Counter(base_source(i) for i in news)
    for src, n in caps.items():
        limit = 1 if src in sources.LOW_SUBSTANCE_SOURCES else MAX_PER_SOURCE
        check(n <= limit, f"{day}: source {src} took {n} slots (cap {limit})")

    # substance floor
    shortest = min((len(i["summary"]) for i in news), default=0)
    check(shortest >= MIN_SUMMARY_CHARS,
          f"{day}: shortest news summary {shortest} < {MIN_SUMMARY_CHARS}")

    # projects are the top-5 by today's stars
    by_stars = sorted((sources._stars_today(i) for i in data["projects"]), reverse=True)[:5]
    got = sorted((sources._stars_today(i) for i in projects), reverse=True)
    check(got == by_stars, f"{day}: project slots not the top-5 by stars_today ({got} vs {by_stars})")

    # determinism: same input -> byte-identical pool
    again = select_candidates(data, now=now)
    check([i["normalized_url"] for i in again["papers"]] == [i["normalized_url"] for i in papers],
          f"{day}: papers pool not deterministic")
    check([i["normalized_url"] for i in again["industry"]["news"]] == [i["normalized_url"] for i in news],
          f"{day}: news pool not deterministic")

    # papers: HF order preserved, arXiv fills the tail
    hf_n = sum(1 for i in papers if base_source(i) == "HuggingFace Papers")
    ax_n = sum(1 for i in papers if base_source(i) == "arXiv")
    print(f"  sources: {dict(caps)}")
    print(f"  papers: HF={hf_n} arXiv={ax_n}")
    if not data["papers"] or ax_n == 0:
        # No arXiv supply: the self-healing quota must hand the whole pool to HF.
        check(hf_n == len(papers), f"{day}: no arXiv input but HF only {hf_n} of {len(papers)}")

    # HF's daily list *is* the ranking, and the deeper pool now reaches further
    # into its tail — so "verbatim order" is load-bearing, not cosmetic. The
    # model is told it may reorder; the pool must not.
    hf_keys = [i["normalized_url"] for i in data["papers"]
               if base_source(i) == "HuggingFace Papers"]
    pool_hf = [i["normalized_url"] for i in papers if base_source(i) == "HuggingFace Papers"]
    check(pool_hf == hf_keys[:hf_n], f"{day}: HF order not preserved in the pool")

    print(f"  shortest news summary: {shortest}")
    print("  top 5 industry:")
    for i in (projects + news)[:5]:
        print(f"    [{base_source(i)}] {i['title'][:70]}")

print("\n" + "=" * 60)
if failures:
    print(f"FAILED ({len(failures)}):")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL PRESELECT CHECKS PASSED")
