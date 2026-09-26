# -*- coding: utf-8 -*-
"""Print the shape of a raw.json (keys, types, sizes) without dumping content."""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

path = sys.argv[1] if len(sys.argv) > 1 else "data/2026-09-26.raw.json"
data = json.load(open(path, encoding="utf-8"))


def shape(obj, indent=0, key=""):
    pad = "  " * indent
    if isinstance(obj, dict):
        print(f"{pad}{key}: dict({len(obj)})")
        for k, v in obj.items():
            shape(v, indent + 1, k)
    elif isinstance(obj, list):
        print(f"{pad}{key}: list({len(obj)})")
        if obj:
            shape(obj[0], indent + 1, "[0]")
    else:
        s = repr(obj)
        print(f"{pad}{key}: {type(obj).__name__} = {s[:70]}")


shape(data)
