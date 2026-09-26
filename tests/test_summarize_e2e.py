"""Stage 3 verification 4: the whole summarize() path, no network, no API key.

_chat is the only HTTP boundary, so it is replaced with canned responses. That
exercises prompt construction, splitting, resolution, assembly, the parser
self-check, the retry loop and the ledger.
"""
import json
import os
import re
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.modules["feedparser"] = types.ModuleType("feedparser")
sys.path.insert(0, "scripts")

import summarize as S  # noqa: E402
from generate_site import parse_digest  # noqa: E402
from sources import select_candidates, PAPER_CANDIDATES  # noqa: E402

REAL_CHAT = S._chat

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print(f"  !! {msg}")


os.environ["API_KEY"] = "test-key"
os.environ.pop("API_BASE_URL", None)
os.environ.pop("API_MODEL", None)

# Canned per-class answers, taken from the ratified format sample.
raw = re.sub(r"<!--.*?-->", "", Path("samples/digest-format-example.md").read_text(encoding="utf-8"))
parts = re.split(r"^## ", raw, flags=re.M)[1:]
CANNED = {}
FILLER = "补充说明与影响分析，这一点对做 Agent 产品的团队尤其值得注意。"


def pad_bodies(text):
    """The sample only carries full bodies for its first 3 items per class; the
    rest are format placeholders. Pad them so the fixture is a realistic answer."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^- \*\*(.+?)\*\*：(.*)$", line)
        if m and len(m.group(2)) < 300:
            body = m.group(2)
            while len(body) < 320:
                body += FILLER
            line = f"- **{m.group(1)}**：{body}"
        out.append(line)
    return "\n".join(out)


for part in parts:
    heading, _, bodytext = part.partition("\n")
    key = "papers" if "论文" in heading else "industry"
    CANNED[key] = pad_bodies(bodytext.split("### 今日观察")[0].strip())
OBSERVATION = "今天的观察内容。这里是对趋势的判断。" * 10


def clip(text, n):
    """Keep the first n *items* of a canned answer, metadata included.

    Counting blank-line-separated chunks would be off by one: the sample also
    carries a `### 📌 备选` heading between its 10th and 11th item, and that is
    its own chunk.
    """
    out, seen = [], 0
    for block in text.split("\n\n"):
        if block.lstrip().startswith("- **"):
            if seen == n:
                break
            seen += 1
        out.append(block)
    return "\n\n".join(out)


def make_chat(mode="ok", calls=None):
    """mode: ok | truncate-once | truncate-always | short | few | empty

    `short` answers with 12 items and `few` with 8. 12 is above the MIN_ITEMS
    soft floor and must ship; 8 is below it and must raise.
    """
    state = {"n": 0}

    def _chat(messages, model, base_url, api_key):
        state["n"] += 1
        if calls is not None:
            calls.append(messages)
        user = messages[1]["content"] if len(messages) > 1 else ""
        if "今日观察" in messages[0]["content"]:
            return OBSERVATION, {"finish_reason": "stop", "attempts": 1, "chars": len(OBSERVATION)}
        which = "papers" if "论文" in user else "industry"
        text = CANNED[which]
        if mode == "truncate-once" and state["n"] == 1:
            return text[:200], {"finish_reason": "length", "attempts": 1, "chars": 200}
        if mode == "truncate-always":
            return text[:200], {"finish_reason": "length", "attempts": 1, "chars": 200}
        if mode == "short":
            return clip(text, 12), {"finish_reason": "stop", "attempts": 1, "chars": 0}
        if mode == "few":
            return clip(text, 8), {"finish_reason": "stop", "attempts": 1, "chars": 0}
        if mode == "empty":
            return "", {"finish_reason": "stop", "attempts": 1, "chars": 0}
        return text, {"finish_reason": "stop", "attempts": 1, "chars": len(text)}

    return _chat


def make_short_answer_stub(bad, items=8, calls=None):
    """The first `bad` class answers carry only `items` items; the rest are full.

    Under the old contract every short answer was a hard failure, so nothing
    ever proved that a retry *recovers* — only that it eventually gives up.
    """
    state = {"classes": 0}

    def _chat(messages, model, base_url, api_key):
        if calls is not None:
            calls.append(messages)
        user = messages[1]["content"] if len(messages) > 1 else ""
        if "今日观察" in messages[0]["content"]:
            return OBSERVATION, {"finish_reason": "stop", "attempts": 1, "chars": len(OBSERVATION)}
        state["classes"] += 1
        text = CANNED["papers" if "论文" in user else "industry"]
        if state["classes"] <= bad:
            text = clip(text, items)
        return text, {"finish_reason": "stop", "attempts": 1, "chars": len(text)}

    return _chat


# Pinned, not read from the working tree: `data/*.raw.json` is live pipeline
# output, so re-running 09-24 would swap the fixture underneath this test and
# quietly change what it asserts. Needs a full clone (not fetch-depth 1).
FIXTURE_REV = "f246ecf6"
data = json.loads(subprocess.run(
    ["git", "show", f"{FIXTURE_REV}:data/2026-09-24.raw.json"],
    capture_output=True, check=True).stdout.decode("utf-8"))["items"]

# Point the canned answers at real pool entries. The sample's own URLs come from
# a different day's fetch, so leaving them makes every item unresolvable — which
# G3 now (correctly) reports as a contract failure. A real answer copies URLs
# out of the candidate list, so the fixture has to as well.
_POOL = select_candidates(
    data,
    now=datetime.fromisoformat("2026-09-24").replace(tzinfo=timezone.utc) + timedelta(days=1),
)


def retarget(text, pool_items):
    out, i = [], 0
    for line in text.splitlines():
        if line.startswith("  - 来源：") and i < len(pool_items):
            src = pool_items[i]
            line = f"  - 来源：[{src['source']}]({src['url']})"
            i += 1
        out.append(line)
    return "\n".join(out)


CANNED["industry"] = retarget(
    CANNED["industry"], _POOL["industry"]["news"] + _POOL["industry"]["projects"])
CANNED["papers"] = retarget(CANNED["papers"], _POOL["papers"])

# ── happy path ──────────────────────────────────────────────────────────────
print("[1] happy path")
S._chat = make_chat("ok")
md, en = S.summarize(data, "2026-09-24")
check(en is None, "second return value is not None")
check(md.startswith("# AI Daily Digest - 2026-09-24"), "missing H1")
check(md.rstrip().endswith("*"), "missing generated-by footer")

digest = parse_digest(md)
cats = [c for c in digest["categories"] if c["items"] or c["alternates"]]
check(len(cats) == 2, f"parsed {len(cats)} classes, want 2")
check([c["type"] for c in cats] == ["news", "paper"], f"class types {[c['type'] for c in cats]}")
check([(len(c["items"]), len(c["alternates"])) for c in cats] == [(10, 5), (10, 5)],
      f"split {[(len(c['items']), len(c['alternates'])) for c in cats]}")
check(len(digest["observation"]) > 50, "observation too short")
check(md.count("### 📌 备选") == 2, f"{md.count('### 📌 备选')} alternates headings, want 2")
# The 09-24 fixture holds 30 HF + 5 arXiv papers, which is exactly the new
# PAPER_CANDIDATES — so this pins the deepened pool, not just its shape.
check(S.LAST_REPORT["pool"] == {"industry_news": 20, "industry_projects": 5,
                                "papers": PAPER_CANDIDATES},
      f"pool {S.LAST_REPORT['pool']}")
check(S.LAST_REPORT["selection"]["industry"] and S.LAST_REPORT["selection"]["papers"],
      "selection not recorded")
check(len(S.LAST_REPORT["calls"]) == 3, f"{len(S.LAST_REPORT['calls'])} calls recorded, want 3")
check(all(c["finish_reason"] == "stop" for c in S.LAST_REPORT["calls"]), "finish_reason not stop")
print(f"  ok: {len(md)} chars, pool {S.LAST_REPORT['pool']}, "
      f"warnings {len(S.LAST_REPORT['warnings'])}")

# ── every item must have a source line ──────────────────────────────────────
print("[2] item shape")
for cat in cats:
    for item in cat["items"] + cat["alternates"]:
        check(item["sources"] and item["sources"][0]["url"].startswith("http"),
              f"item without source: {item['title'][:30]}")
        check(item["star_count"] >= 1, f"item without stars: {item['title'][:30]}")
        check(len(item["desc"]) > 0, f"item without body: {item['title'][:30]}")
    check(all(not i["value"] for i in cat["items"]), "核心价值 still present")

# ── retry on truncation, then success ───────────────────────────────────────
print("[3] truncation recovery")
calls = []
S._chat = make_chat("truncate-once", calls)
md, _ = S.summarize(data, "2026-09-24")
check(len(S.LAST_REPORT["calls"]) == 4, f"expected 4 calls after a retry, got {len(S.LAST_REPORT['calls'])}")
check(len(parse_digest(md)["categories"]) == 2, "retry did not produce a full document")
print(f"  ok: recovered, {len(S.LAST_REPORT['calls'])} calls")

# ── persistent truncation must raise ────────────────────────────────────────
print("[4] persistent truncation")
S._chat = make_chat("truncate-always")
try:
    S.summarize(data, "2026-09-24")
    check(False, "persistent truncation did not raise")
except RuntimeError as exc:
    check("截断" in str(exc), f"unexpected error: {exc}")
    print(f"  ok: raised -> {str(exc)[:70]}")

# ── below the soft floor still raises ───────────────────────────────────────
print("[5] below the soft floor")
S._chat = make_chat("few")
try:
    S.summarize(data, "2026-09-24")
    check(False, "8-item answer did not raise")
except RuntimeError as exc:
    check("条目数" in str(exc), f"unexpected error: {exc}")
    print(f"  ok: raised -> {str(exc)[:70]}")

# ── 12 items, once a hard failure, now ships ────────────────────────────────
print("[5b] soft floor: a 12-item answer ships")
S._chat = make_chat("short")
md, _ = S.summarize(data, "2026-09-24")
split = [(len(c["items"]), len(c["alternates"]))
         for c in parse_digest(md)["categories"] if c["items"] or c["alternates"]]
check(split == [(10, 2), (10, 2)], f"12-item split {split}, want [(10, 2), (10, 2)]")
check(md.count("### 📌 备选") == 2, f"{md.count('### 📌 备选')} alternates headings, want 2")
check(len(S.LAST_REPORT["calls"]) == 3, f"{len(S.LAST_REPORT['calls'])} calls, want 3 (no retry)")
print(f"  ok: shipped short, {len(md)} chars, {len(S.LAST_REPORT['warnings'])} warnings")

# ── the retry says how many are missing (the run-7 fix) ─────────────────────
print("[5c] retry names the shortfall")
sfx = S._retry_suffix(["条目数为 8，少于下限 10"], 15, 10, produced=8)
check("只写了 8 条" in sfx and "还差 7 条" in sfx, f"_retry_suffix omits the deficit: {sfx[:90]}")
check("还差" not in S._retry_suffix(["条目数不足"], 15, 10),
      "_retry_suffix invents a deficit when the produced count is unknown")

calls = []
S._chat = make_short_answer_stub(1, calls=calls)
md, _ = S.summarize(data, "2026-09-24")
retry = calls[1][-1]["content"] if len(calls) > 1 else ""
check(len(calls) == 4, f"{len(calls)} calls after one short answer, want 4")
check("只写了 8 条" in retry and "还差 7 条" in retry,
      f"the retry prompt never says what is missing: {retry[-90:]}")
check([(len(c["items"]), len(c["alternates"])) for c in parse_digest(md)["categories"]
       if c["items"] or c["alternates"]] == [(10, 5), (10, 5)],
      "the retry did not restore a full document")
print("  ok: 8 -> 15 items on retry, deficit stated")

# ── FORMAT_ATTEMPTS=3: two bad answers, the third lands ─────────────────────
print("[5d] two bad answers, third attempt")
S._chat = make_short_answer_stub(2)
md, _ = S.summarize(data, "2026-09-24")
check(len(S.LAST_REPORT["calls"]) == 5, f"{len(S.LAST_REPORT['calls'])} calls, want 5")
check(len([c for c in parse_digest(md)["categories"] if c["items"]]) == 2,
      "the third attempt did not produce a document")
print(f"  ok: recovered on attempt 3, {len(S.LAST_REPORT['calls'])} calls")

# ── more than 15 is truncated, not retried ──────────────────────────────────
print("[5e] over-long answer is truncated")
# A 16th item shaped like a real one — same block, real pool URL.
extra = "\n\n" + CANNED["industry"].split("\n\n")[0].replace("- **", "- **溢出条目 ", 1)


def overlong_chat():
    def _chat(messages, model, base_url, api_key):
        user = messages[1]["content"] if len(messages) > 1 else ""
        if "今日观察" in messages[0]["content"]:
            return OBSERVATION, {"finish_reason": "stop", "attempts": 1, "chars": len(OBSERVATION)}
        which = "papers" if "论文" in user else "industry"
        return CANNED[which] + extra, {"finish_reason": "stop", "attempts": 1, "chars": 0}

    return _chat


S._chat = overlong_chat()
md, _ = S.summarize(data, "2026-09-24")
check(len(S.LAST_REPORT["calls"]) == 3, f"{len(S.LAST_REPORT['calls'])} calls, want 3 (no retry)")
check(any("截断" in w for w in S.LAST_REPORT["warnings"]), "the truncation was not warned about")
check("溢出条目" not in md, "the truncated 16th item leaked into the document")
check([(len(c["items"]), len(c["alternates"])) for c in parse_digest(md)["categories"]
       if c["items"] or c["alternates"]] == [(10, 5), (10, 5)], "truncated split is wrong")
print("  ok: 16 -> 15 items, no retry")

# ── empty answer must raise ─────────────────────────────────────────────────
print("[6] empty answer")
S._chat = make_chat("empty")
try:
    S.summarize(data, "2026-09-24")
    check(False, "empty answer did not raise")
except RuntimeError as exc:
    print(f"  ok: raised -> {str(exc)[:70]}")

# ── missing key ─────────────────────────────────────────────────────────────
print("[7] missing API key")
os.environ.pop("API_KEY", None)
try:
    S.summarize(data, "2026-09-24")
    check(False, "missing API_KEY did not raise")
except ValueError as exc:
    print(f"  ok: raised -> {exc}")
os.environ["API_KEY"] = "test-key"

# ── transport retry ─────────────────────────────────────────────────────────
print("[8] transport failure retried")
import requests  # noqa: E402

state = {"n": 0}


class FakeResp:
    status_code = 200

    def __init__(self, text):
        self._text = text

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._text}, "finish_reason": "stop"}],
                "usage": {"completion_tokens": 1}}


def fake_post(url, headers=None, json=None, timeout=None):
    """Patched in below the _chat boundary so its own retry loop is exercised."""
    state["n"] += 1
    if state["n"] == 1:
        raise requests.Timeout("simulated")
    msgs = json["messages"]
    user = msgs[1]["content"] if len(msgs) > 1 else ""
    if "今日观察" in msgs[0]["content"]:
        return FakeResp(OBSERVATION)
    return FakeResp(CANNED["papers" if "论文" in user else "industry"])


S._chat = REAL_CHAT                  # un-stub, so the real retry loop runs
S.requests.post = fake_post          # ...against a stubbed transport
md, _ = S.summarize(data, "2026-09-24")
check(len(parse_digest(md)["categories"]) == 2, "transport retry did not recover")
print("  ok: recovered after a simulated timeout")

# ── gluing: several items written onto one physical line ────────────────────
# Replays the real 2026-09-26 shape: the model appended the next item's title
# and body to the tail of the previous item's line and never wrote that item's
# own 重要性/来源 lines. Papers shipped 11 items where 15 had been written.
print("[9] glued answer: every item is recovered")


def pool_answer(pool_items, rewrite_title=None, n_pos=10, n_alt=5):
    """An answer whose titles and URLs both come from the real pool.

    The sample fixture's titles are from another day, so a split-out item —
    which is verified by title — could never match against this pool. Gluing
    tests need items the pool actually knows.
    """
    blocks = []
    for i, src in enumerate(pool_items[:n_pos + n_alt]):
        title = rewrite_title(i, src) if rewrite_title else src["title"]
        body = (f"{title} 的正文。" + "补充说明与影响分析。" * 24 if i < n_pos
                else f"{title} 的一句话要点。")
        blocks.append(f"- **{title}**：{body}\n"
                      f"  - 重要性：★★★★☆ / 5\n"
                      f"  - 来源：[{src['source']}]({src['url']})")
    return "\n\n".join(blocks)


def glue(text, positions):
    """Splice the given items onto the tail of the previous item's line.

    The glued item's own block is dropped entirely — its 重要性/来源 lines were
    never written, and reproducing that is the point: it leaves the recovered
    item with no 来源 of its own, which is exactly what the verification then
    has to deal with.
    """
    blocks = text.split("\n\n")
    for i in sorted(positions, reverse=True):
        prev = blocks[i - 1].split("\n")
        prev[0] += "。" + blocks[i].split("\n")[0]
        blocks[i - 1] = "\n".join(prev)
        del blocks[i]
    return "\n\n".join(blocks)


PAPERS_PLAIN = pool_answer(_POOL["papers"])
PAPERS_GLUED = glue(PAPERS_PLAIN, [1, 2, 6, 13])
INDUSTRY_PLAIN = pool_answer(
    _POOL["industry"]["news"] + _POOL["industry"]["projects"],
    rewrite_title=lambda i, src: f"改写后的行业标题第 {i + 1} 条")
INDUSTRY_GLUED = glue(INDUSTRY_PLAIN, [3, 9])


def fixed_chat(papers_text, industry_text, observation=OBSERVATION):
    def _chat(messages, model, base_url, api_key):
        user = messages[1]["content"] if len(messages) > 1 else ""
        if "今日观察" in messages[0]["content"]:
            return observation, {"finish_reason": "stop", "attempts": 1, "chars": len(observation)}
        which = papers_text if "论文" in user else industry_text
        return which, {"finish_reason": "stop", "attempts": 1, "chars": len(which)}

    return _chat


S._chat = fixed_chat(PAPERS_GLUED, INDUSTRY_PLAIN)
md, _ = S.summarize(data, "2026-09-24")
split = [(len(c["items"]), len(c["alternates"]))
         for c in parse_digest(md)["categories"] if c["items"] or c["alternates"]]
check(split == [(10, 5), (10, 5)],
      f"4 glued paper items were not recovered: {split}")
check(len(S.LAST_REPORT["calls"]) == 3, f"{len(S.LAST_REPORT['calls'])} calls, want 3 (no retry)")
check(any("拆分" in w for w in S.LAST_REPORT["warnings"]),
      "the split was not recorded in the ledger")
papers_out = [c for c in parse_digest(md)["categories"] if c["type"] == "paper"][0]
check(all(i["sources"] for i in papers_out["items"] + papers_out["alternates"]),
      "a recovered paper shipped without a 来源 line")
print(f"  ok: 11 -> 15 items, every recovered paper kept a source")

print("[10] glued industry: recovered items degrade, the day still ships")
S._chat = fixed_chat(PAPERS_PLAIN, INDUSTRY_GLUED)
md, _ = S.summarize(data, "2026-09-24")
ind_out = [c for c in parse_digest(md)["categories"] if c["type"] == "news"][0]
check((len(ind_out["items"]), len(ind_out["alternates"])) == (10, 5),
      f"industry gluing changed the count: {len(ind_out['items'])}/{len(ind_out['alternates'])}")
check(any("降级删除来源行" in w for w in S.LAST_REPORT["warnings"]),
      f"no degradation was recorded: {[w for w in S.LAST_REPORT['warnings'] if 'industry' in w]}")
# Every other industry item is URL-resolved, so only the recovered ones may be
# missing a link — and they were dropped rather than pointed at a guess.
missing = [i["title"] for i in ind_out["items"] + ind_out["alternates"] if not i["sources"]]
check(len(missing) == 2, f"{len(missing)} industry items lost their link, want 2: {missing}")
print(f"  ok: 2 recovered items degraded to no link, no raise")

print("[11] a recovered paper that cannot be verified retries, then raises")
# Corrupt the title of the item that actually gets glued (index 1), not whichever
# title happens to look recognisable.
victim = _POOL["papers"][1]["title"]
bad = glue(PAPERS_PLAIN.replace(f"- **{victim}**：", f"- **{victim} NOT-IN-POOL**："), [1])
S._chat = fixed_chat(bad, INDUSTRY_PLAIN)
try:
    S.summarize(data, "2026-09-24")
    check(False, "an unverifiable split did not raise")
except RuntimeError as exc:
    check("无法在论文候选池中精确匹配" in str(exc), f"unexpected error: {exc}")
    check("重试" in str(exc), f"it did not retry first: {exc}")
    print(f"  ok: raised -> {str(exc)[:76]}")

print("[12] the observation cap warns without blocking")
long_obs = "今日观察的内容与判断。" * 36          # 396 chars, past the 350 budget
check(len(long_obs) > S.OBSERVATION_MAX_CHARS, "the long observation is not long")
S._chat = fixed_chat(PAPERS_PLAIN, INDUSTRY_PLAIN, observation=long_obs)
md, _ = S.summarize(data, "2026-09-24")
check(any("今日观察" in w or "[observation]" in w for w in S.LAST_REPORT["warnings"]),
      f"an over-budget observation was not warned about: {S.LAST_REPORT['warnings']}")
check(len(parse_digest(md)["observation"]) > 50, "the observation was dropped")
print(f"  ok: {len(long_obs)} chars warned, day shipped")

print("\n" + "=" * 60)
if failures:
    print(f"FAILED ({len(failures)}):")
    for f in failures:
        print("  -", f.splitlines()[0])
    sys.exit(1)
print("ALL SUMMARIZE E2E CHECKS PASSED")
