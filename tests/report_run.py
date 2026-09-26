# -*- coding: utf-8 -*-
"""Acceptance data for the first post-fix cloud run (36249700116, commit f246ecf6).

Reads both revisions straight out of git so the working tree is never touched.
"""
import json
import statistics
import subprocess
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "scripts")
from generate_site import parse_digest  # noqa: E402

DATE = "2026-09-26"
# Explicit revs, never `origin/main`: that ref MOVES, and the 2026-09-26 re-run
# (b3f4a7a2) replaced the glued artifact with a clean one, so comparing against
# it now reports "no gluing" for the run this script exists to document.
OLD, NEW = "4bda296c", "f246ecf6"


def show(rev, path):
    out = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True)
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8")


def bucket(n):
    if n < 200:
        return "<200   !!"
    if n < 250:
        return "200-249 !"
    if n < 300:
        return "250-299"
    if n <= 400:
        return "300-400 ok"
    if n <= 500:
        return "401-500"
    return ">500    !"


def lengths(cat, which):
    return [len(i["desc"]) for i in cat[which]]


def report(tag, raw_json, md):
    print("=" * 72)
    print(tag)
    print("=" * 72)
    digest = parse_digest(md)
    cats = [c for c in digest["categories"] if c["items"] or c["alternates"]]

    for cat in cats:
        pos, alt = cat["items"], cat["alternates"]
        lp = lengths(cat, "items")
        la = lengths(cat, "alternates")
        print(f"\n[{cat['name']}]  正选 {len(pos)} 条 / 备选 {len(alt)} 条 / 合计 {len(pos)+len(alt)}")
        if lp:
            print(f"  正选正文: min={min(lp)} 中位={int(statistics.median(lp))} max={max(lp)} "
                  f"均值={statistics.mean(lp):.0f}")
            hist = Counter(bucket(n) for n in lp)
            for k in sorted(hist):
                print(f"    {k:<12} {hist[k]:>2}  {'#' * hist[k]}")
        if la:
            print(f"  备选正文: min={min(la)} 中位={int(statistics.median(la))} max={max(la)}")
        # source resolution, measured on the shipped document
        nosrc = [i["title"][:34] for i in pos + alt if not i["sources"]]
        print(f"  未定位到来源: {len(nosrc)}/{len(pos)+len(alt)}"
              f"  ({len(nosrc)/max(1,len(pos)+len(alt)):.0%})")
        for t in nosrc:
            print(f"    - {t}")

    obs = digest.get("observation") or ""
    print(f"\n今日观察: {len(obs)} 字")

    if raw_json:
        raw = json.loads(raw_json)
        sel = raw.get("selection") or {}
        print(f"\n候选池/账本: pool={raw.get('pool')} 选中="
              f"{ {k: len(v) for k, v in sel.items()} }")
        warns = raw.get("warnings") or []
        print(f"告警 {len(warns)} 条:")
        for w in warns:
            print(f"  ! {w}")


report("【改造后】f246ecf6  (run 36249700116)", show(NEW, f"data/{DATE}.raw.json"), show(NEW, f"daily/{DATE}.md"))
report("【改造前】4bda296c  (run 36204898180)", show(OLD, f"data/{DATE}.raw.json"), show(OLD, f"daily/{DATE}.md"))
