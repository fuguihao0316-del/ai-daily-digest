"""Stage 3 verification 2/3: post-processing, assembly, self-check, render."""
import re
import sys
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.modules["feedparser"] = types.ModuleType("feedparser")
sys.path.insert(0, "scripts")

import generate_site  # noqa: E402
import summarize as S  # noqa: E402
from generate_site import parse_digest  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print(f"  !! {msg}")
    return cond


# ── Load the ratified format sample as ground truth ─────────────────────────
raw = Path("samples/digest-format-example.md").read_text(encoding="utf-8")
raw = re.sub(r"<!--.*?-->", "", raw, flags=re.S)

parts = re.split(r"^## ", raw, flags=re.M)[1:]          # two class bodies
classes = []
for part in parts:
    heading, _, bodytext = part.partition("\n")
    bodytext = bodytext.split("### 今日观察")[0]        # observation excluded
    classes.append((heading.strip(), bodytext))

reference = []          # (items+alternates) per class, from the real parser
for heading, bodytext in classes:
    digest = parse_digest("## " + heading + "\n" + bodytext)
    cat = digest["categories"][0]
    reference.append(cat["items"] + cat["alternates"])

print(f"sample: {len(classes)} classes, {[len(r) for r in reference]} items")


# ── A. split round-trip ─────────────────────────────────────────────────────
print("\n[A] split round-trip")
split_refs = []
for i, (heading, bodytext) in enumerate(classes):
    items, warns = S._split_items(bodytext)
    split_refs.append(items)
    ref = reference[i]
    check(len(items) == len(ref), f"class {i}: split {len(items)} != ref {len(ref)}")
    for n, (got, want) in enumerate(zip(items, ref), 1):
        check(got["title"] == want["title"], f"class {i} #{n}: title mismatch")
        check(got["body"] == want["desc"], f"class {i} #{n}: body mismatch\n     got={got['body'][:60]!r}\n    want={want['desc'][:60]!r}")
    # the sample carries the real `### 📌 备选` heading; dropping it is correct
    unexpected = [w for w in warns if "备选" not in w]
    check(not unexpected, f"class {i}: unexpected warnings {unexpected}")
print(f"  bodies matched byte-for-byte: {sum(len(r) for r in split_refs)}")


# ── B. reflow a hard-wrapped body ───────────────────────────────────────────
print("\n[B] reflow after hard wrap")
for width in (120, 60, 40):
    exact = 0
    total = 0
    for i, (heading, bodytext) in enumerate(classes):
        wrapped = []
        for line in bodytext.splitlines():
            if len(line) > width and not line.strip().startswith("-"):
                wrapped += [line[j:j + width] for j in range(0, len(line), width)]
            else:
                wrapped.append(line)
        items, _ = S._split_items("\n".join(wrapped))
        ref = reference[i]
        check(len(items) == len(ref), f"width {width} class {i}: {len(items)} items != {len(ref)}")
        for got, want in zip(items, ref):
            total += 1
            if got["body"] == want["desc"]:
                exact += 1
    print(f"  width {width}: {exact}/{total} bodies restored exactly")
    if width == 120:
        check(exact == total, f"width 120: only {exact}/{total} restored")


# ── C. adversarial model output ─────────────────────────────────────────────
print("\n[C] adversarial output")


def block(title, body, stars="★★★★☆ / 5", source="- 来源：[X](https://x.com/a)"):
    return f"- **{title}**：{body}\n  - 重要性：{stars}\n  {source}"


base = [block(f"标题{n}", f"正文{n}" + "嗯" * 200) for n in range(1, 16)]
clean = "\n\n".join(base)
items, warns = S._split_items(clean)
check(len(items) == 15, f"clean text gave {len(items)} items")

# echoed class heading + preamble must not become a phantom category
noisy = f"## 🗞 AI 行业动态\n\n好的，以下是整理结果：\n\n{clean}"
items, warns = S._split_items(noisy)
check(len(items) == 15, f"echoed heading gave {len(items)} items")
check(any("标题行" in w for w in warns), "echoed heading was not warned about")

# model-emitted alternates heading must be dropped (code inserts its own)
withalt = clean.replace("- **标题11**", "### 📌 备选\n\n- **标题11**")
items, _ = S._split_items(withalt)
check(len(items) == 15, f"model alternates heading gave {len(items)} items")
serialized = S._serialize_class("## 🗞 AI 行业动态", items, 10)
check(serialized.count(S.ALTERNATES_HEADING) == 1,
      f"serialized has {serialized.count(S.ALTERNATES_HEADING)} alternates headings")
check(serialized.index(S.ALTERNATES_HEADING) < serialized.index("标题11"),
      "alternates heading not before item 11")

# code fences
items, _ = S._split_items("```markdown\n" + clean + "\n```")
check(len(items) == 15, f"fenced text gave {len(items)} items")

# deprecated 核心价值 line -> warn, structure unchanged
deprecated = clean.replace("  - 重要性：", "  - 核心价值：重要\n  - 重要性：", 1)
items, _ = S._split_items(deprecated)
check(len(items) == 15, f"deprecated field gave {len(items)} items")
fw, unresolved = S._finalize(items, [])
check(len(items) == 15 and unresolved == 15,
      f"empty pool -> every item unresolved ({unresolved}/15)")
check(any("核心价值" in w for w in fw), "deprecated field still warns")
check(all(it["source_line"] == "" for it in items),
      "empty pool -> no source line survives")

# inline bold inside a body must NOT start a new item
inline = clean.replace("正文1" + "嗯" * 200, "正文1 含 **加粗** 字样的正文" + "嗯" * 200, 1)
items, _ = S._split_items(inline)
check(len(items) == 15, f"inline bold gave {len(items)} items")

# A real title can run well past 100 characters. The line-start bound used to be
# 80, and a title over it was folded into the *previous* item's body, taking its
# 重要性/来源 lines with it — the same silent amputation as gluing. Measured pool
# maxima: 119 chars (papers), 141 (industry).
LONG_TITLE = ("Tri-PvP: Exposing Modality Bias in Omni-Modal Large Language Models "
              "through Perceptual-Prompting with Pseudo-Visual Prototypes")
check(len(LONG_TITLE) > 100, "the long-title fixture is not actually long")
long_items, long_warns = S._split_items(
    "\n\n".join([base[0], block(LONG_TITLE, "正文" + "嗯" * 200)] + base[2:]))
check(len(long_items) == 15, f"long title gave {len(long_items)} items, want 15")
check(long_items[1]["title"] == LONG_TITLE, "a >100-char title was not parsed as an item")
check(LONG_TITLE not in long_items[0]["body"], "the long title was folded into item 1's body")
check(not [w for w in long_warns if "上限" in w], f"the length bound still fired: {long_warns}")

# Gluing: the model writes several items onto one physical line. Only the item
# that *begins* a line used to survive, so the rest were swallowed whole and
# shipped as over-long bodies. Shape copied from the real 2026-09-26 output.
glued_source = ("- **甲**：正文甲" + "嗯" * 200 + "。- **乙**：正文乙" + "嗯" * 200
                + "。- **丙**：正文丙" + "嗯" * 200
                + "\n  - 重要性：★★★★☆ / 5\n  - 来源：[X](https://x.com/a)")
glued_items, glued_warns = S._split_items(glued_source)
check(len(glued_items) == 3, f"glued line gave {len(glued_items)} items, want 3")
check([i["title"] for i in glued_items] == ["甲", "乙", "丙"],
      f"glued titles {[i['title'] for i in glued_items]}")
check([i["split_out"] for i in glued_items] == [False, True, True],
      f"split_out flags {[i['split_out'] for i in glued_items]}")
check(any("拆分" in w for w in glued_warns), f"gluing was not warned about: {glued_warns}")
# 正文X (3) + 200 个「嗯」, and only the first two keep the 「。」 that separated
# them on the glued line.
check([len(i["body"]) for i in glued_items] == [204, 204, 203],
      f"glued bodies {[len(i['body']) for i in glued_items]}")
check(all(i["body"].startswith(f"正文{t}") for i, t in zip(glued_items, "甲乙丙")),
      f"a split body did not keep its own text: {[i['body'][:6] for i in glued_items]}")
# the metadata block that follows belongs to the item that starts the line
check(glued_items[0]["source_line"] and not glued_items[1]["source_line"]
      and not glued_items[2]["source_line"],
      "the trailing metadata did not stay with the line's first item")

# Counts: only the soft floor is a hard failure. 12 items is a short but
# shippable answer, and 16 is truncated (with a warning) by _run_class before
# _validate_class ever sees it — so the upper bound deliberately is not
# checked here. The three counts must land on different sides of the line.
extra = base + [block("标题16", "正文16" + "嗯" * 200)]
for n, pool, wants_problem in ((8, base[:8], True), (12, base[:12], False), (16, extra, False)):
    items, _ = S._split_items("\n\n".join(pool))
    check(len(items) == n, f"expected {n} parsed items, got {len(items)}")
    problems, _ = S._validate_class(items, 15, 10)
    got = any("条目数" in p for p in problems)
    check(got == wants_problem, f"{n} items: count problem {got}, want {wants_problem}")

# amputation fingerprint: many short positives must be a hard problem
short = [block(f"标题{n}", "太短了") for n in range(1, 6)] + base[5:]
items, _ = S._split_items("\n\n".join(short))
problems, warnings = S._validate_class(items, 15, 10)
check(any("系统性截断" in p for p in problems), "5 short bodies did not trigger the amputation guard")
check(len(warnings) >= 5, "short bodies produced no warnings")


# ── D. star parsing ─────────────────────────────────────────────────────────
print("\n[D] star parsing")
for raw_stars, want in [("★★★★★ / 5", 5), ("★★★☆☆ / 5", 3), ("- 重要性：4 / 5", 4), (None, None)]:
    check(S._parse_stars(raw_stars) == want, f"stars {raw_stars!r} -> {S._parse_stars(raw_stars)} != {want}")


# ── E. serialize + parser self-check ────────────────────────────────────────
print("\n[E] assembly + parser self-check")
doc = S._assemble("2026-09-26", "deepseek-chat",
                  [S._serialize_class("## 🗞 AI 行业动态", split_refs[0], 10),
                   S._serialize_class("## 📄 学术论文研究动态", split_refs[1], 10)],
                  "今天的观察内容。" * 20)
check(S._check_document(doc, [(10, 5), (10, 5)]) == [],
      f"self-check rejected the reference document: {S._check_document(doc, [(10, 5), (10, 5)])}")

parsed = parse_digest(doc)
check([c["type"] for c in parsed["categories"]] == ["news", "paper"],
      f"class types {[c['type'] for c in parsed['categories']]}")
check([(len(c["items"]), len(c["alternates"])) for c in parsed["categories"]] == [(10, 5), (10, 5)],
      f"split {[(len(c['items']), len(c['alternates'])) for c in parsed['categories']]}")
check(parsed["observation"], "observation lost")

# negative cases
bad = doc.replace("### 📌 备选", "", 1)
check(S._check_document(bad, [(10, 5), (10, 5)]), "missing alternates heading not caught")
check(S._check_document(doc, [(10, 5)]), "extra class not caught")
check(S._check_document(doc.replace("今天的观察内容。", "", 1) and doc.split("### 今日观察")[0],
                        [(10, 5), (10, 5)]), "missing observation not caught")


# ── F. rendering ────────────────────────────────────────────────────────────
print("\n[F] rendering")
html = generate_site.build_digest_body(parsed, "zh")
check('class="alternates"' in html, "alternates block not rendered")
check("备选 5 条" in html, "alternates label missing")
check(html.count('class="category"') == 2, f'{html.count(chr(34)+"category"+chr(34))} category blocks')
check('data-cat="news"' in html and 'data-cat="paper"' in html, "filter types wrong")
check("item-value" not in html, "deprecated 核心价值 still rendered")

# html escaping of model text
nasty_text = "\n\n".join(
    [block("标题1", "含 <script> & 符号的正文" + "嗯" * 180)]
    + [block(f"标题{n}", "正文" + "嗯" * 200) for n in range(2, 16)]
)
nasty_items, _ = S._split_items(nasty_text)
nasty_doc = S._assemble("2026-09-26", "m", [
    S._serialize_class("## 🗞 AI 行业动态", nasty_items, 10),
    S._serialize_class("## 📄 学术论文研究动态", split_refs[1], 10),
], "观察内容。" * 30)
html2 = generate_site.build_digest_body(parse_digest(nasty_doc), "zh")
check("<script>" not in html2, "model text was injected into HTML unescaped")
check("&lt;script&gt;" in html2, "escaping did not produce entities")


# ── G. audio fallback length ────────────────────────────────────────────────
print("\n[G] audio fallback")
for _stub in ("edge_tts", "mutagen", "mutagen.mp3"):
    sys.modules.setdefault(_stub, types.ModuleType(_stub))
import audio  # noqa: E402
script = audio._fallback_script(doc, "2026-09-26")
check(600 <= len(script) <= 1000, f"fallback script is {len(script)} chars, want 600-1000")
check("备选" not in script, "fallback read the alternates")


print("\n" + "=" * 60)
if failures:
    print(f"FAILED ({len(failures)}):")
    for f in failures:
        print("  -", f.splitlines()[0])
    sys.exit(1)
print("ALL FORMAT CHECKS PASSED")
