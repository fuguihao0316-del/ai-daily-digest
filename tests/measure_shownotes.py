# -*- coding: utf-8 -*-
"""Measure build_shownotes output: new format vs the 159 historical days."""
import sys
from pathlib import Path

sys.path.insert(0, "scripts")
from generate_site import parse_digest, build_shownotes

sample = Path("samples/digest-format-example.md")
digest = parse_digest(sample.read_text(encoding="utf-8"))
notes = build_shownotes(digest, "2026-09-26")

print("=== NEW FORMAT (samples/digest-format-example.md) ===")
print("chars:", len(notes))
print("lines:", len(notes.splitlines()))
for cat in digest["categories"]:
    print(f"  {cat['name']}: {len(cat['items'])} items, "
          f"alternates={len(cat['alternates'])}")
print()
print("--- first 600 chars ---")
print(notes[:600])
print()
print("--- item lines only ---")
for line in notes.splitlines():
    if line.startswith("· "):
        print(f"  [{len(line):5d}] {line[:60]}...")
print()

print("=== OLD FORMAT (historical daily/*.md) ===")
lens = []
for f in sorted(Path("daily").glob("*.md")):
    if f.name.endswith(".en.md"):
        continue
    d = parse_digest(f.read_text(encoding="utf-8"))
    lens.append((len(build_shownotes(d, f.stem)), f.stem))
lens.sort()
print(f"days: {len(lens)}")
print(f"min: {lens[0][0]} ({lens[0][1]})")
print(f"median: {lens[len(lens)//2][0]} ({lens[len(lens)//2][1]})")
print(f"max: {lens[-1][0]} ({lens[-1][1]})")
