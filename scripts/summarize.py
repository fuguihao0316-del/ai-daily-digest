"""
AI summarization - supports DeepSeek / OpenAI / any OpenAI-compatible API.

Output contract (see samples/digest-format-example.md): exactly two classes,
15 items each, sorted by importance. Items 1-10 carry a <=400-character Chinese
body; items 11-15 are one-liners folded behind a `### 📌 备选` heading that
this module inserts itself — the model never decides who is an alternate.

Chinese only. The digest used to be produced bilingually in a *single* call
deliminated by `===ENGLISH===`; that is gone, and so is the failure mode it
hid: with both languages in one 8192-token response, an overrun truncated the
tail silently instead of erroring.
"""

import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from generate_site import parse_digest
from sources import SUMMARY_MAX_CHARS, normalize_title, normalize_url, select_candidates


ITEMS_PER_CLASS = 15      # the target the model is asked for
POSITIVE_COUNT = 10
ALTERNATE_COUNT = ITEMS_PER_CLASS - POSITIVE_COUNT
MIN_ITEMS = 10            # soft floor. The model does not reliably reach 15 — it kept
                          # stopping at 9-10 papers rather than dipping below its own
                          # quality bar. At >=10 we still ship: the positives are the
                          # first POSITIVE_COUNT items and the alternates shrink to
                          # whatever is left. Below MIN_ITEMS the brief was ignored.
                          # The split is computed by code, never by the model.
ALTERNATES_HEADING = "### 📌 备选"
OBSERVATION_HEADING = "### 今日观察"

MAX_TITLE_CHARS = 80

# Body-length guard rails. The lower bound is the load-bearing one: a body that
# got split across two lines is silently amputated by parse_digest while the
# document still *looks* structurally perfect (measured: 268-301 chars lost per
# body, no structural trace), so length is the only thing that detects it.
SHORT_BODY = 200          # an amputated body lands at ~70-100, but the model also just
                          # writes short ones — a 53-char positive shipped on 2026-09-26.
                          # 200 catches both without pretending to judge style.
MEAGRE_BODY = 250         # conforming but under the 300-400 budget
MIN_AVG_BODY = 250        # mean positive body below this is systematic under-writing,
                          # and catches it even when every body clears SHORT_BODY
LONG_BODY = 500
ALTERNATE_BODY = 140
MAX_SHORT_BODIES = 2      # 3+ amputated bodies is systematic, not a thin item
RESOLVE_FAIL_LIMIT = 0.30  # share of items whose 来源 cannot be traced to the pool

MAX_TOKENS = 8192         # DeepSeek's ceiling — per-class calls never approach it
REQUEST_TIMEOUT = 300
MAX_ATTEMPTS = 3          # transport errors
FORMAT_ATTEMPTS = 3       # two retries — the count rule needs the extra shot

# Filled by summarize() and read by main.py. A module-level ledger rather than
# a third return value, because main.py unpacks exactly two.
LAST_REPORT = {"model": None, "calls": [], "warnings": [], "pool": {}}


SYSTEM_PROMPT_CLASS = """你是 AI 领域的资深编辑，面向 AI 从业者、研究者和开发者撰写每日简报的一个板块。

【输出格式】以下每一条都必须严格遵守，任何一条不满足都会导致整个板块重写：

1. 只输出条目本身。不要输出任何以 # 开头的标题行（# / ## / ### / #### 都不行），
   不要输出「备选」「候选」之类的小标题，不要写开场白、结束语或任何解释。
2. 不要用 ``` 代码块包裹输出。
3. 每条恰好三行，格式如下（第二、三行开头是两个空格）：
- **中文标题**：正文，标题与正文必须在同一行，正文无论多长都不得换行
  - 重要性：★★★★☆ / 5
  - 来源：[来源名](URL)
4. 正文内部不得出现换行，不得以 - 或数字加点开头，不得使用 ** 加粗或任何 Markdown 标记。
5. 元数据只有「重要性」和「来源」两行，不要输出「核心价值」或其他任何字段。
6. 只从候选清单里挑选条目。URL 必须逐字复制候选清单中的 URL，不得改写、拼接或编造。
7. 不得编造数字、日期、机构、引语或事实。候选清单里没有的信息一律不写。
8. 全文用中文，专有名词（模型名、公司名、产品名、论文名）保留英文原文。
9. 不要输出英文版本，不要输出 ===ENGLISH=== 或任何分隔符。

【正文写法】正文不是摘要的复述，而是要讲清楚三件事：发生了什么、关键细节（具体数字、
机构名、时间）、以及它对读者意味着什么。要有信息密度，不要写「值得关注」「意义重大」
这类空话。"""

SYSTEM_PROMPT_OBSERVATION = """你是 AI 领域的资深编辑。请为今天的简报写一段「今日观察」。

要求：
1. 3-4 句话，总长 300 字以内。
2. 要有观点和判断，点出今天最值得关注的趋势或信号，不要逐条复述内容。
3. 可以把几件事串起来看，指出它们共同指向什么、有什么值得警惕或期待。
4. 只依据给出的事实，不得编造。不要写「值得关注」「意义重大」这类空话。
5. 直接输出正文段落。不要输出标题、不要分点、不要用任何 Markdown 标记。"""


CLASSES = [
    {
        "key": "industry",
        "name": "AI 行业动态",
        "heading": "## 🗞 AI 行业动态",
        "blocks": [("候选：行业新闻", "news"), ("候选：开源项目", "projects")],
        "focus": """分档处理候选（条数优先，只丢真正不合格的）：
A. 优先选择：重大模型/产品发布、产业动向（融资、并购、监管、人事）、
   技术圈正在热议的话题或知名研究者/创始人的明确观点。
B. 有点内容但价值一般的：**不要丢弃**，排在 A 档之后。
C. 只有这些才真正丢弃：无实质内容的观点文与预测文、纯炒作标题，
   以及同一件事的重复报道（只保留信息最全的一条）。""",
    },
    {
        "key": "papers",
        "name": "学术论文研究动态",
        "heading": "## 📄 学术论文研究动态",
        "blocks": [("候选：学术论文", "papers")],
        "focus": """分档处理候选论文（条数优先，只丢真正不合格的）：
A. 优先选择：有明确方法、结论或基准的论文，尤其是提出新架构、新任务定义、
   新评测基准，或刷新了现有 SOTA 的工作。
B. 增量小、创新有限但仍可陈述的：**不要丢弃**，排在 A 档之后，落到备选位。
C. 只有这些才真正丢弃：纯综述、纯立场文、结论含糊到无法陈述的。
候选清单已被 HuggingFace 社区热度排序，可以参考这个顺序，但请按你自己的判断重排。""",
    },
]


# ── Prompt construction ─────────────────────────────────────────────────────────

def format_candidates(blocks):
    """Render the candidate pool as numbered blocks, 6 lines per candidate.

    Numbering is global within the class so the model has a stable handle to
    refer to, and so a retry can name specific entries.
    """
    lines = []
    n = 0
    for title, items in blocks:
        if not items:
            continue
        lines.append(f"=== {title}（{len(items)} 条）===")
        for item in items:
            n += 1
            lines.append(
                f"[{n:02d}] 来源: {item['source']}\n"
                f"标题: {item['title']}\n"
                f"URL: {item['url']}\n"
                f"发布时间: {item.get('published') or '未知'}\n"
                f"初判重要性: {item.get('importance_hint', 3)}/5\n"
                f"摘要: {(item.get('summary') or '')[:SUMMARY_MAX_CHARS]}\n"
            )
    return "\n".join(lines)


def _class_user_prompt(cls, block_text, date_str, total, positives, projects):
    alternates = total - positives
    rules = [
        f"1. 共 {total} 条，按重要性从高到低排列。**条数不足是最严重的错误。**",
        f"2. 第 1-{positives} 条写完整正文，每条 300-400 字。",
    ]
    if alternates:
        rules.append(
            f"3. 第 {positives + 1}-{total} 条是备选，只写 1-2 句（约 80 字），"
            "只陈述事实，不展开分析，但星级要保留真实值，不因为是备选就降级。"
        )
    rules.append(
        f"{len(rules) + 1}. 必须产出恰好 {total} 条。候选是按重要度预筛过的，"
        "即使某条你觉得平庸，也把它排在后面照写——"
        "**不要为了回避平庸候选而少写条数**，那会被直接判为不合格并重写。"
    )
    n = len(rules)
    if cls["key"] == "industry" and len(projects) >= 2:
        rules.append(
            f"{n + 1}. 这 {total} 条中**至少 2 条必须来自「候选：开源项目」**"
            "（即 GitHub 开源仓库），其余从「候选：行业新闻」里选。"
        )
        n += 1
    rules.append(f"{n + 1}. {cls['focus']}")

    return f"""今天是 {date_str}。

以下是今天抓取的候选（已按重要度预筛）。请从中挑出 {total} 条，写成「{cls['name']}」板块。

{block_text}

【本板块要求】
{chr(10).join(rules)}"""


def _retry_suffix(problems, total, positives, produced=None):
    alternates = total - positives
    detail = "；".join(problems)
    # Name the shortfall explicitly. "共 15 条" alone did not move the model:
    # it went 9 -> 10 items across two attempts on 2026-09-26.
    deficit = ""
    if produced is not None and produced < total:
        deficit = (f"\n\n你上一次只写了 {produced} 条，**还差 {total - produced} 条**。"
                   f"这次必须写满 {total} 条。")
    return f"""你上一次的输出不合格：{detail}{deficit}

请重新完整输出一次，严格遵守格式，不要输出任何标题行、代码块或解释：
每条恰好三行——`- **标题**：正文`（标题与正文必须同一行，正文不得换行）、
`  - 重要性：★★★★☆ / 5`、`  - 来源：[来源名](URL)`（URL 逐字复制候选清单）。
共 {total} 条：前 {positives} 条正文 300-400 字，后 {alternates} 条只写 1-2 句。"""


# ── Model output → item blocks ──────────────────────────────────────────────────

_FENCE_RE = re.compile(r"^\s*```[A-Za-z0-9_+-]*\s*$")
_HEADING_RE = re.compile(r"^#{1,6}\s")
_ITEM_RE = re.compile(r"^-\s+\*\*(.+?)\*\*\s*[：:]?\s*(.*)$")
_META_RE = re.compile(r"^[\-\*]\s+(重要性|核心价值|来源)\s*[：:]")
_URL_RE = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)|(https?://\S+)")
_ASCII_WORD = re.compile(r"[A-Za-z0-9]")
_CJK_END = "。，、；：！？）》」』…—"


def _join_body(body, continuation):
    """Reflow a body the model wrapped across lines.

    A separator is needed between two Latin words, but not between CJK
    characters — and never right after CJK sentence punctuation. Measured on
    samples/digest-format-example.md wrapped at 120 columns: this rule restores
    30/30 bodies byte-for-byte.

    This is only a safety net. The real defence is prompt rule 4 plus the
    body-length check below.
    """
    if not body:
        return continuation
    if not continuation:
        return body
    left, right = body[-1], continuation[0]
    if left in _CJK_END:
        return body + continuation
    if _ASCII_WORD.match(left) or _ASCII_WORD.match(right):
        return body + " " + continuation
    return body + continuation


def _split_items(text):
    """Parse the model's answer into item blocks.

    Returns (items, warnings). Anything that is not an item, metadata, or a
    continuation line is dropped with a warning — notably echoed headings,
    which the parser would otherwise turn into a phantom one-item category.
    """
    items = []
    warnings = []
    current = None

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped or _FENCE_RE.match(line):
            continue

        if _HEADING_RE.match(line):
            warnings.append(f"丢弃模型输出的标题行：{stripped[:40]}")
            continue

        # Item lines must start at column 0; the 2-space indent on metadata is
        # what keeps them apart.
        match = _ITEM_RE.match(line)
        if match and len(match.group(1)) <= MAX_TITLE_CHARS:
            current = {
                "title": match.group(1).strip().strip("[]").strip(),
                "body": match.group(2).strip(),
                "stars": "",
                "source_line": "",
                "deprecated": False,
            }
            items.append(current)
            continue

        if current is None:
            warnings.append(f"丢弃开场白：{stripped[:40]}")
            continue

        meta = _META_RE.match(stripped)
        if meta:
            kind = meta.group(1)
            if kind == "重要性":
                current["stars"] = stripped
            elif kind == "来源":
                current["source_line"] = stripped
            else:
                current["deprecated"] = True
            continue

        current["body"] = _join_body(current["body"], stripped)

    return items, warnings


def _extract_url(line):
    match = _URL_RE.search(line or "")
    if not match:
        return ""
    return (match.group(1) or match.group(2) or "").rstrip(")。，、")


def _resolve(item, candidates):
    """Work out which pool entry the model actually meant.

    Exact URL first, then exact title. No fuzzy matching: industry headlines are
    rewritten into Chinese, so a similarity match would happily point at the
    wrong story — a broken link is the lesser evil.
    """
    url = _extract_url(item.get("source_line"))
    if url:
        key = normalize_url(url)
        for cand in candidates:
            if key and normalize_url(cand["url"]) == key:
                return cand

    title = normalize_title(item.get("title"))
    if title:
        for cand in candidates:
            if normalize_title(cand["title"]) == title:
                return cand
    return None


def _parse_stars(raw):
    if not raw:
        return None
    filled = raw.count("★")
    if filled:
        return max(1, min(5, filled))
    match = re.search(r"([1-5])\s*/\s*5", raw)
    return int(match.group(1)) if match else None


def _finalize(items, candidates):
    """Rewrite 来源 from the pool, drop what cannot be verified, normalize stars.

    Returns (warnings, unresolved), where `unresolved` counts the items whose
    source line could not be traced back to the candidate pool. The caller turns
    that count into the contract-failure decision — see RESOLVE_FAIL_LIMIT.
    """
    warnings = []
    unresolved = 0
    for item in items:
        cand = _resolve(item, candidates)
        if cand is None:
            unresolved += 1
            warnings.append(f"来源无法在候选池中定位，已删除来源行：{item['title'][:30]}")
            # The URL is the one field _resolve exists to verify, so the model's
            # own copy of it is exactly what must not be trusted here. Shipping
            # the item with no link beats shipping a link we could not confirm.
            item["source_line"] = ""
        else:
            item["source_line"] = f"- 来源：[{cand['source']}]({cand['url']})"

        stars = _parse_stars(item.get("stars"))
        if stars is None:
            stars = (cand or {}).get("importance_hint", 3)
            warnings.append(f"星级缺失，用 importance_hint 兜底：{item['title'][:30]}")
        item["stars"] = "★" * stars + "☆" * (5 - stars) + " / 5"

        if item.get("deprecated"):
            warnings.append(f"模型输出了已废弃的「核心价值」行：{item['title'][:30]}")
    return warnings, unresolved


def _validate_class(items, total, positives):
    """Returns (problems, warnings). Problems mean retry, then raise."""
    problems = []
    warnings = []

    if len(items) < MIN_ITEMS:
        problems.append(f"条目数为 {len(items)}，少于下限 {MIN_ITEMS}")

    short = 0
    for n, item in enumerate(items, 1):
        body = item["body"]
        if not body:
            problems.append(f"第 {n} 条没有正文")
            continue
        if n <= positives:
            if len(body) < SHORT_BODY:
                short += 1
                warnings.append(f"第 {n} 条正文仅 {len(body)} 字，疑似换行被吞：{item['title'][:24]}")
            elif len(body) < MEAGRE_BODY:
                warnings.append(f"第 {n} 条正文 {len(body)} 字，低于 300-400 字预算")
            elif len(body) > LONG_BODY:
                warnings.append(f"第 {n} 条正文 {len(body)} 字，超出 400 字预算")
        elif len(body) > ALTERNATE_BODY:
            warnings.append(f"第 {n} 条是备选但正文 {len(body)} 字，超出 80 字预算")

    if short > MAX_SHORT_BODIES:
        problems.append(f"有 {short} 条正选正文过短，判定为系统性截断")

    # A mean check, on top of the per-body one: every body can clear SHORT_BODY
    # while the class as a whole is still written to half the budget.
    pos_bodies = [len(i["body"]) for i in items[:positives] if i["body"]]
    if pos_bodies:
        avg = sum(pos_bodies) / len(pos_bodies)
        if avg < MIN_AVG_BODY:
            problems.append(f"正选正文平均 {avg:.0f} 字，低于 {MIN_AVG_BODY} 字预算")

    return problems, warnings


def _serialize_class(heading, items, positives):
    """Render one class, inserting `### 📌 备选` ourselves.

    Position decides who is an alternate, so the split is fixed by code and
    cannot drift between runs or depend on the model's judgement.
    """
    blocks = []
    for item in items:
        lines = [f"- **{item['title']}**：{item['body']}"]
        lines.append(f"  - 重要性：{item['stars']}")
        if item["source_line"]:
            lines.append(f"  {item['source_line']}")
        blocks.append("\n".join(lines))

    if len(blocks) <= positives:
        return "\n\n".join([heading] + blocks)
    return "\n\n".join(
        [heading] + blocks[:positives] + [ALTERNATES_HEADING] + blocks[positives:]
    )


# ── API ─────────────────────────────────────────────────────────────────────────

def _chat(messages, model, base_url, api_key):
    """One call, retried on transport errors. Returns (text, meta)."""
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": MAX_TOKENS,
    }

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code in (400, 401, 403):
                # Config problem, not a transient one — retrying cannot help.
                raise RuntimeError(
                    f"API rejected the request ({resp.status_code}): {resp.text[:300]}"
                )
            resp.raise_for_status()
        except RuntimeError:
            raise
        except Exception as exc:
            last_error = exc
            print(f"  [summarize] 第 {attempt}/{MAX_ATTEMPTS} 次请求失败：{exc}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(2 * attempt ** 2)
            continue

        payload_json = resp.json()
        choice = (payload_json.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        meta = {
            "attempts": attempt,
            "finish_reason": choice.get("finish_reason"),
            "completion_tokens": (payload_json.get("usage") or {}).get("completion_tokens"),
            "chars": len(text),
            "ms": int((time.monotonic() - started) * 1000),
        }
        return text, meta

    raise RuntimeError(f"API 连续 {MAX_ATTEMPTS} 次调用失败：{last_error}")


def _run_class(cls, blocks, date_str, model, base_url, api_key):
    """Ask the model for one class.

    Returns (items, candidates, warnings, meta, positives): `positives` is the
    headline/alternate split point, which the caller needs both to serialize the
    class and to state what parse_digest should later agree with.

    `projects` is derived from `blocks` rather than stashed on `cls` — CLASSES is
    module level, so writing to it would leak state across calls.
    """
    candidates = [item for _, items in blocks for item in items]
    total = min(ITEMS_PER_CLASS, len(candidates))
    positives = min(POSITIVE_COUNT, total)
    projects = next((items for title, items in blocks if "开源" in title), [])

    base_messages = [
        {"role": "system", "content": SYSTEM_PROMPT_CLASS},
        {"role": "user", "content": _class_user_prompt(
            cls, format_candidates(blocks), date_str, total, positives, projects)},
    ]

    last_problems = ["未知错误"]
    produced = None
    for attempt in range(1, FORMAT_ATTEMPTS + 1):
        messages = base_messages
        if attempt > 1:
            messages = base_messages + [
                {"role": "user", "content": _retry_suffix(
                    last_problems, total, positives, produced=produced)}
            ]

        text, meta = _chat(messages, model, base_url, api_key)
        LAST_REPORT["calls"].append({"purpose": cls["key"], **meta})
        print(f"  [{cls['key']}] {meta['chars']} 字符 / finish={meta['finish_reason']}")

        if meta.get("finish_reason") == "length":
            last_problems = ["输出被截断（finish_reason=length）"]
            print(f"  [{cls['key']}] {last_problems[0]}，重试")
            continue

        items, warnings = _split_items(text)
        if len(items) > ITEMS_PER_CLASS:
            warnings.append(f"条目数 {len(items)} 超出 {ITEMS_PER_CLASS}，已截断末尾")
            items = items[:ITEMS_PER_CLASS]
        produced = len(items)
        # The split is computed here, never taken from the model, and never taken
        # from `positives` either — that one still describes what the *prompt*
        # asked for, and base_messages was built from it outside this loop.
        actual_positives = min(POSITIVE_COUNT, len(items))
        finalize_warnings, unresolved = _finalize(items, candidates)
        warnings += finalize_warnings
        problems, body_warnings = _validate_class(items, total, actual_positives)
        warnings += body_warnings

        # Graded, deliberately. A handful of unverifiable links is survivable —
        # those items ship without a 来源 line rather than with a link nobody
        # checked. But once a third of them fail, the model has stopped copying
        # URLs out of the candidate list, and every unverified link in the class
        # is suspect: that is a contract failure and takes the retry/raise path.
        if items and unresolved / len(items) >= RESOLVE_FAIL_LIMIT:
            problems.append(
                f"{unresolved}/{len(items)} 条来源无法在候选池中定位"
                f"（{unresolved / len(items):.0%}，达到 {RESOLVE_FAIL_LIMIT:.0%} 阈值）"
            )

        if not problems:
            return items, candidates, warnings, meta, actual_positives

        last_problems = problems
        print(f"  [{cls['key']}] 不合格：{'；'.join(problems)}")

    raise RuntimeError(
        f"[{cls['key']}] 重试 {FORMAT_ATTEMPTS} 次后仍不合格：{'；'.join(last_problems)}"
    )


def _run_observation(chosen, date_str, model, base_url, api_key):
    """Summarise both classes into the closing `### 今日观察` paragraph."""
    lines = []
    for heading, items in chosen:
        lines.append(f"=== {heading} ===")
        for item in items:
            gist = item["body"].split("。")[0][:60]
            lines.append(f"- {item['title']}：{gist}")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_OBSERVATION},
        {"role": "user", "content": f"今天是 {date_str}。以下是今天简报的全部条目：\n\n"
                                    + "\n".join(lines)},
    ]
    text, meta = _chat(messages, model, base_url, api_key)
    LAST_REPORT["calls"].append({"purpose": "observation", **meta})
    print(f"  [observation] {meta['chars']} 字符 / finish={meta['finish_reason']}")

    if meta.get("finish_reason") == "length" or not text.strip():
        raise RuntimeError("今日观察生成失败（截断或空响应）")

    return re.sub(r"\s+", " ", _strip_markdown(text)).strip()


def _strip_markdown(text):
    text = re.sub(r"^#{1,6}\s+", "", text.strip(), flags=re.M)
    return re.sub(r"[*`]+", "", text)


# ── Assembly and final self-check ───────────────────────────────────────────────

def _assemble(date_str, model, class_markdown, observation):
    parts = [f"# AI Daily Digest - {date_str}", ""]
    for md in class_markdown:
        parts += [md, ""]
    parts += [
        OBSERVATION_HEADING, "", observation, "",
        "---",
        f"*Generated by AI Daily Digest using {model}*",
    ]
    return "\n".join(parts) + "\n"


def _check_document(markdown, expected):
    """Feed the assembled document back through the real parser.

    parse_digest is the contract, so if it and the assembler disagree the day
    must not ship. `expected` is a list of (positives, alternates) per class.
    """
    digest = parse_digest(markdown)
    cats = [c for c in digest["categories"] if c["items"] or c["alternates"]]

    problems = []
    if len(cats) != len(expected):
        problems.append(f"解析出 {len(cats)} 个类别，期望 {len(expected)}")
    else:
        for cat, (positives, alternates) in zip(cats, expected):
            if len(cat["items"]) != positives:
                problems.append(f"「{cat['name']}」正选 {len(cat['items'])} 条，期望 {positives}")
            if len(cat["alternates"]) != alternates:
                problems.append(f"「{cat['name']}」备选 {len(cat['alternates'])} 条，期望 {alternates}")
    if not digest["observation"]:
        problems.append("今日观察为空")
    return problems


def summarize(data, date_str):
    """Generate the daily digest via an OpenAI-compatible API (DeepSeek default).

    Returns (zh_markdown, None). The second element is always None — English is
    no longer generated — but the 2-tuple shape stays because main.py unpacks it.
    """
    LAST_REPORT.update({"model": None, "calls": [], "warnings": [], "pool": {},
                        "selection": {}})

    api_key = os.environ.get("API_KEY")
    if not api_key:
        raise ValueError("API_KEY environment variable is required")

    base_url = os.environ.get("API_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("API_MODEL", "deepseek-chat")
    LAST_REPORT["model"] = model

    # Derive "now" from the digest date rather than the wall clock, so the same
    # raw data always selects the same pool.
    try:
        now = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc) + timedelta(days=1)
    except ValueError:
        now = datetime.now(timezone.utc)

    pool = select_candidates(data, now=now)
    LAST_REPORT["pool"] = {
        "industry_news": len(pool["industry"]["news"]),
        "industry_projects": len(pool["industry"]["projects"]),
        "papers": len(pool["papers"]),
    }
    # Which items the model actually got to see. Recorded so "why wasn't X
    # included?" is answerable later; the pool itself is reproducible from the
    # raw trace plus this date.
    LAST_REPORT["selection"] = {
        "industry": [i["normalized_url"]
                     for i in pool["industry"]["news"] + pool["industry"]["projects"]],
        "papers": [i["normalized_url"] for i in pool["papers"]],
    }
    print(f"候选池：行业新闻 {LAST_REPORT['pool']['industry_news']} + "
          f"开源项目 {LAST_REPORT['pool']['industry_projects']}，"
          f"论文 {LAST_REPORT['pool']['papers']}")

    if not any(pool["industry"].values()) and not pool["papers"]:
        return (f"# AI Daily Digest - {date_str}\n\n> No content fetched today.\n", None)

    print(f"调用 {model}：每个板块一次，共 3 次。")
    class_markdown = []
    expected = []
    chosen = []

    for cls in CLASSES:
        blocks = [(title, pool["industry"][key] if key in pool["industry"] else pool[key])
                  for title, key in cls["blocks"]]
        items, candidates, warnings, meta, positives = _run_class(
            cls, blocks, date_str, model, base_url, api_key)
        LAST_REPORT["warnings"] += [f"[{cls['key']}] {w}" for w in warnings]

        total = len(items)
        class_markdown.append(_serialize_class(cls["heading"], items, positives))
        expected.append((positives, total - positives))
        chosen.append((cls["name"], items))

        bodies = [len(i["body"]) for i in items]
        print(f"  [{cls['key']}] 产出 {total} 条，正文长度 "
              f"{min(bodies)}-{max(bodies)} 字，告警 {len(warnings)} 条")

    print("生成今日观察...")
    observation = _run_observation(chosen, date_str, model, base_url, api_key)

    markdown = _assemble(date_str, model, class_markdown, observation)

    problems = _check_document(markdown, expected)
    if problems:
        raise RuntimeError("拼装结果未通过解析器自检：" + "；".join(problems))

    if LAST_REPORT["warnings"]:
        print(f"告警 {len(LAST_REPORT['warnings'])} 条：")
        for warning in LAST_REPORT["warnings"]:
            print(f"  ! {warning}")
    else:
        print("无告警。")

    return markdown, None
