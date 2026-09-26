# -*- coding: utf-8 -*-
"""Rebuild the site, then report the real creator-page show-notes sizes."""
import re
import sys
from pathlib import Path

sys.path.insert(0, "scripts")
from generate_site import generate_site, parse_digest, build_shownotes

generate_site(root=Path("."))

page = Path("docs/creator.html").read_text(encoding="utf-8")
notes = re.findall(r'<pre class="notes" id="n-([\d-]+)">(.*?)</pre>', page, re.S)
print(f"\ncreator.html: {len(page)} bytes, {len(notes)} episodes")
sizes = sorted((len(n), d) for d, n in notes)
print(f"  show notes: min={sizes[0][0]} ({sizes[0][1]})")
print(f"              median={sizes[len(sizes)//2][0]} ({sizes[len(sizes)//2][1]})")
print(f"              max={sizes[-1][0]} ({sizes[-1][1]})")

# Worst case: a fully-populated new-format digest, not a placeholder sample.
sample = parse_digest(Path("samples/digest-format-example.md").read_text(encoding="utf-8"))
FILL = "这是一段用于压力测试的正文内容，讲清楚发生了什么以及它对读者意味着什么。"
for cat in sample["categories"]:
    for it in cat["items"] + cat["alternates"]:
        while len(it["desc"]) < 400:
            it["desc"] += FILL
worst = build_shownotes(sample, "2026-09-26")
print(f"\nworst case (all 30 bodies padded to 400 chars): {len(worst)} chars")
bullets = [ln for ln in worst.splitlines() if ln.startswith("· ")]
print(f"  entries={len(bullets)}  longest={max(len(b) for b in bullets)}")
