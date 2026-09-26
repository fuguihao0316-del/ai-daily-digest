# -*- coding: utf-8 -*-
"""F1 (index-style show notes) and G3 (graded source-resolution failure)."""
import sys
import types

sys.modules.setdefault("feedparser", types.ModuleType("feedparser"))
sys.path.insert(0, "scripts")

import generate_site as G
import summarize as S

FAILS = []


def check(cond, label):
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        FAILS.append(label)


# ── F1: _gist ───────────────────────────────────────────────────────────────────

print("[F1] _gist")
check(G._gist("短句。后面还有很长的内容" * 20) == "短句。", "short first sentence kept whole")
check(G._gist("") == "", "empty body -> empty")
check(G._gist("   ") == "", "whitespace body -> empty")

long_one = "这是一个非常长的从句没有句号一直写下去" * 10
g = G._gist(long_one)
check(len(g) <= G.SHOWNOTE_GIST_CHARS + 1, f"long body cut (len={len(g)})")
check(g.endswith("…"), "long body ends with ellipsis")

no_stop = "no period anywhere at all just words " * 10
g2 = G._gist(no_stop)
check(g2.endswith("…") and len(g2) <= G.SHOWNOTE_GIST_CHARS + 1, "no 。 -> cut + ellipsis")

# Trailing punctuation must not sit before the ellipsis.
padded = "，" * 30 + "字" * 200
g3 = G._gist(padded)
check("，…" not in g3, "trailing punctuation stripped before ellipsis")

# The 。 sits exactly at the limit boundary: keep it, do not add an ellipsis.
boundary = "字" * (G.SHOWNOTE_GIST_CHARS - 1) + "。" + "更多"
check(G._gist(boundary).endswith("。"), "sentence ending exactly at limit is kept")

# ── F1: build_shownotes ─────────────────────────────────────────────────────────

print("[F1] build_shownotes")
from pathlib import Path

digest = G.parse_digest(Path("samples/digest-format-example.md").read_text(encoding="utf-8"))
notes = build = G.build_shownotes(digest, "2026-09-26")

positives = sum(len(c["items"]) for c in digest["categories"])
alternates = sum(len(c.get("alternates", [])) for c in digest["categories"])
bullets = [ln for ln in notes.splitlines() if ln.startswith("· ")]
check(len(bullets) == positives + alternates,
      f"indexes positives AND alternates ({len(bullets)} = {positives}+{alternates})")
check(all("\n" not in b for b in bullets), "each entry is a single line")
check(len(notes) < 6000, f"index-sized, not full text (len={len(notes)})")
check(G.build_shownotes(digest, "2026-09-26") == notes, "deterministic")

# The biggest single bullet must be a one-liner, not a 400-char body.
longest = max(len(b) for b in bullets)
check(longest < 200, f"longest entry stays a one-liner ({longest} chars)")

# ── G3: _finalize resolution accounting ─────────────────────────────────────────

print("[G3] _finalize")
CANDS = [
    {"source": f"Src{i}", "url": f"https://ex.com/{i}", "title": f"Title {i}",
     "importance_hint": 4}
    for i in range(20)
]


def model_output(n, n_bad, body="正文" * 160, alt_body="短" * 40):
    lines = []
    good = n - n_bad
    for i in range(n):
        url = f"https://ex.com/{i}" if i < good else f"https://bogus.example/{i}"
        lines.append(f"- **标题{i}**：{body if i < 10 else alt_body}")
        lines.append("  - 重要性：★★★★☆ / 5")
        lines.append(f"  - 来源：[Src{i}]({url})")
    return "\n".join(lines)


items, _ = S._split_items(model_output(15, 4))
warnings, unresolved = S._finalize(items, CANDS)
check(unresolved == 4, f"counts unresolved ({unresolved})")
check(all(w.startswith("来源无法在候选池中定位，已删除来源行") for w in warnings[:4]),
      "warning says the line was dropped")
check(items[0]["source_line"] == "- 来源：[Src0](https://ex.com/0)", "resolved line rewritten")
check(items[14]["source_line"] == "", "unresolved line cleared, model URL not kept")
check(not any("bogus.example" in it["source_line"] for it in items),
      "no unverified URL survives anywhere")

# ── G3: the 30% gate inside _run_class ──────────────────────────────────────────

print("[G3] _run_class gate")
CLS = {"key": "industry", "name": "AI 行业动态", "focus": "test",
       "blocks": [("候选：行业新闻", "news")]}


def drive(n_cands, n_bad, limit=None):
    """Run _run_class against a stubbed model. Returns (raised, message)."""
    if limit is not None:
        S.RESOLVE_FAIL_LIMIT = limit
    cands = CANDS[:n_cands]
    out = model_output(n_cands, n_bad)
    S._chat = lambda *a, **k: (out, {"attempts": 1, "finish_reason": "stop",
                                     "completion_tokens": 10, "chars": len(out), "ms": 1})
    S.FORMAT_ATTEMPTS = 1
    try:
        S._run_class(CLS, [("候选：行业新闻", cands)], "2026-09-26", "m", "u", "k")
        return False, ""
    except RuntimeError as exc:
        return True, str(exc)
    finally:
        S.RESOLVE_FAIL_LIMIT = 0.30
        S.FORMAT_ATTEMPTS = 2


raised, msg = drive(15, 4)
check(not raised, "26.7% failure ships (degraded, no raise)")

raised, msg = drive(15, 5)
check(raised, "33.3% failure raises")
check("33%" in msg or "5/15" in msg, f"error names the ratio: {msg[:90]}")

raised, msg = drive(10, 3, limit=0.30)
check(raised, "exactly 30.0% raises (>= boundary)")

raised, msg = drive(15, 0)
check(not raised, "0% failure ships")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("  -", f)
    raise SystemExit(1)
print("ALL F/G CHECKS PASSED")
