"""
Data source fetchers - all free, no AI tokens needed.
Each function returns a list of dicts with stable fields for traceability.
"""

import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests


AI_KEYWORDS = [
    "ai", "ml", "llm", "gpt", "transformer", "neural", "deep-learning",
    "machine-learning", "nlp", "diffusion", "agent", "rag", "embedding",
    "model", "inference", "fine-tun", "lora", "vision", "multimodal",
    "chatbot", "langchain", "openai", "anthropic", "gemini", "claude",
]

HIGH_SIGNAL_KEYWORDS = [
    "release", "launch", "open source", "benchmark", "state-of-the-art",
    "sota", "reasoning", "inference", "agent", "multimodal", "model",
    "api", "eval", "safety", "chip", "funding", "acquisition",
    "发布", "开源", "融资", "收购", "基准", "推理", "模型", "智能体",
    "多模态", "安全", "芯片",
]

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}

# Source text is the raw material for a ~400-character write-up, and the old
# 500-char cap left the model with too little to work from — it filled the gap
# by inventing. main.py projects this back down to 500 before committing
# data/*.raw.json, so raising it costs nothing in repo size.
SUMMARY_MAX_CHARS = 1500


def clean_text(value, max_chars=500):
    """Strip HTML and whitespace noise from source summaries."""
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def normalize_url(url):
    """Normalize URLs so the same story is easier to dedupe."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path.rstrip("/"),
        urlencode(query, doseq=True),
        "",
    ))


def normalize_title(title):
    return re.sub(r"\W+", "", (title or "").lower())


def estimate_importance(category, title, summary, source):
    """Give the model a transparent first-pass signal without replacing editorial judgment."""
    text = f"{title} {summary} {source}".lower()
    score = 3

    if category in {"papers", "projects"}:
        score += 1
    if any(keyword in text for keyword in HIGH_SIGNAL_KEYWORDS):
        score += 1
    if any(name in text for name in ["openai", "anthropic", "google", "deepmind", "meta", "microsoft", "nvidia"]):
        score += 1

    return max(1, min(5, score))


def make_item(category, title, url, summary, source, published=None, metadata=None):
    cleaned_summary = clean_text(summary, max_chars=SUMMARY_MAX_CHARS)
    return {
        "category": category,
        "title": clean_text(title, max_chars=220),
        "url": url or "",
        "normalized_url": normalize_url(url),
        "summary": cleaned_summary,
        "source": source,
        "published": published,
        "importance_hint": estimate_importance(category, title, cleaned_summary, source),
        "metadata": metadata or {},
    }


def dedupe_items(items):
    """Deduplicate by normalized URL first, then normalized title."""
    seen = {}
    url_index = {}
    title_index = {}

    for item in items:
        url_key = item.get("normalized_url")
        title_key = normalize_title(item.get("title"))
        key = url_index.get(url_key) or title_index.get(title_key)

        if not key:
            key = url_key or title_key
        if not key:
            continue

        previous = seen.get(key)
        if not previous:
            seen[key] = item
            if url_key:
                url_index[url_key] = key
            if title_key:
                title_index[title_key] = key
            continue

        if len(item.get("summary", "")) > len(previous.get("summary", "")):
            item["source"] = f"{previous['source']}, {item['source']}"
            item["importance_hint"] = max(previous["importance_hint"], item["importance_hint"])
            seen[key] = item
            if url_key:
                url_index[url_key] = key
            if title_key:
                title_index[title_key] = key
        else:
            previous["source"] = f"{previous['source']}, {item['source']}"
            previous["importance_hint"] = max(previous["importance_hint"], item["importance_hint"])

    return list(seen.values())


# ── Candidate pre-selection ─────────────────────────────────────────────────────
# Runs before the LLM. A normal day's raw pool is ~190 items (39 papers, ~154
# news/projects) and each summary is now up to 1500 chars, so the whole firehose
# cannot go into one prompt. This trims each class down to a deterministic
# 25-item pool that the model picks its 15 from — and the 5 alternates come from
# the same pool. Pure offline logic: no network, no clock beyond `now`.

CANDIDATES_PER_CLASS = 25  # industry pool only: 20 news + 5 projects
PAPER_CANDIDATES = 35      # papers get a deeper pool than industry. With only 25 to
                           # choose from, the model kept judging the tail "增量过小"
                           # and stopped at 9-10 items instead of the 15 it was asked
                           # for (2026-09-26 first cloud run). More headroom means it
                           # can reach the target without having to dip below its bar.
PROJECT_SLOTS = 5          # reserved inside the industry pool (>=2 must survive into the final 15)
HF_QUOTA = 30              # papers: HuggingFace's own community ranking gets the lion's share
ARXIV_QUOTA = 5            # arXiv is the uncurated safety net
MAX_PER_SOURCE = 3         # no single feed may dominate the pool
MAX_PER_SOURCE_LOW = 1     # community feeds: content-free summaries, mostly chatter
LOW_SUBSTANCE_SOURCES = {"Hacker News", "r/LocalLLaMA"}
MIN_SUMMARY_CHARS = 120    # below this there is not enough material for a 400-char body


def base_source(item):
    """The feed an item originally came from.

    dedupe_items joins duplicates as "HuggingFace Papers, arXiv", so every
    source-based decision has to look at the first name only.
    """
    return (item.get("source") or "").split(",")[0].strip()


def _published_ts(item):
    raw = item.get("published")
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _recency_bucket(item, now_ts):
    ts = _published_ts(item)
    if not ts:
        return 3
    age_hours = (now_ts - ts) / 3600
    if age_hours < 24:
        return 0
    if age_hours < 48:
        return 1
    if age_hours < 72:
        return 2
    return 3


def _keyword_hits(item):
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    return sum(1 for kw in HIGH_SIGNAL_KEYWORDS if kw in text)


def _stars_today(item):
    """Today's star gain, as scraped off github.com/trending."""
    raw = str((item.get("metadata") or {}).get("stars_today") or "").replace(",", "").strip()
    return int(raw) if raw.isdigit() else 0


def _lifetime_stars(item):
    raw = (item.get("metadata") or {}).get("stars") or 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def rank_papers(papers, limit=CANDIDATES_PER_CLASS):
    """HuggingFace's daily list is already a community-upvote ranking, and its
    order survives dedupe (dict insertion order), so keep it verbatim.

    Recency is worthless as a paper signal here: the 09-24 pool legitimately
    contains a 34-day-old paper that HF still ranked. arXiv gets a small ranked
    quota instead, as a safety net for papers HF's top list missed.
    """
    hf, arxiv, other = [], [], []
    for item in papers:
        src = base_source(item)
        if src == "HuggingFace Papers":
            hf.append(item)
        elif src == "arXiv":
            arxiv.append(item)
        else:
            other.append(item)

    arxiv.sort(key=lambda i: (
        -i.get("importance_hint", 3),
        -_published_ts(i),
        normalize_title(i.get("title")),
    ))

    # The two max() calls make the quotas self-healing: on a day arXiv returns
    # nothing (09-20/21/22 in the fixtures), HF fills the whole pool instead.
    n_hf = min(len(hf), max(HF_QUOTA, limit - min(len(arxiv), ARXIV_QUOTA)))
    n_ax = min(len(arxiv), max(ARXIV_QUOTA, limit - n_hf))

    picked = hf[:n_hf] + arxiv[:n_ax]
    for item in other + hf[n_hf:] + arxiv[n_ax:]:
        if len(picked) >= limit:
            break
        picked.append(item)
    return picked[:limit]


def _news_key(item, now_ts):
    """importance_hint alone is nearly useless — 62 of 143 news items on 09-24
    tie at 4 — so it is only the first of six tie-breakers. The last two make
    the ordering total, which is what keeps the pool reproducible.
    """
    return (
        -item.get("importance_hint", 3),
        -_keyword_hits(item),
        -(min(len(item.get("summary") or ""), 400) // 100),
        _recency_bucket(item, now_ts),
        base_source(item),
        normalize_title(item.get("title")),
    )


def _project_key(item):
    today = _stars_today(item)
    return (
        0 if today else 1,   # trending-page items (today's stars) outrank OSSInsight's
        -today,
        -_lifetime_stars(item),   # only ever compared between two lifetime-count items
        -item.get("importance_hint", 3),
        normalize_title(item.get("title")),
    )


def rank_news(news, need, now_ts):
    """Fill `need` slots in three passes, so a thin day still fills the pool:

    1. enough material for a 400-char body + per-source cap
    2. substance floor released, cap still on
    3. cap released
    """
    ordered = sorted(news, key=lambda i: _news_key(i, now_ts))
    picked = []
    seen = set()

    for floor, capped in ((MIN_SUMMARY_CHARS, True), (0, True), (0, False)):
        counts = {}
        for item in picked:
            src = base_source(item)
            counts[src] = counts.get(src, 0) + 1

        for item in ordered:
            if len(picked) >= need:
                return picked
            key = item.get("normalized_url") or normalize_title(item.get("title"))
            if key in seen:
                continue
            if len(item.get("summary") or "") < floor:
                continue
            src = base_source(item)
            if capped:
                cap = MAX_PER_SOURCE_LOW if src in LOW_SUBSTANCE_SOURCES else MAX_PER_SOURCE
                if counts.get(src, 0) >= cap:
                    continue
            seen.add(key)
            counts[src] = counts.get(src, 0) + 1
            picked.append(item)

    return picked[:need]


def select_candidates(data, limit=CANDIDATES_PER_CLASS, now=None):
    """The deterministic pre-filter: the LLM sees these pools, not the firehose.

    `now` drives the recency buckets and is supplied by the caller (summarize.py
    derives it from the digest date) rather than read off the wall clock, so the
    same raw JSON always yields the same pool.
    """
    now_ts = (now or datetime.now(timezone.utc)).timestamp()

    projects = sorted(data.get("projects") or [], key=_project_key)[:PROJECT_SLOTS]
    news = rank_news(data.get("news") or [], limit - len(projects), now_ts)

    return {
        "industry": {"news": news, "projects": projects},
        "papers": rank_papers(data.get("papers") or [], PAPER_CANDIDATES),
    }


def fetch_huggingface_papers():
    """Fetch today's papers from HuggingFace Daily Papers API."""
    items = []
    try:
        resp = requests.get("https://huggingface.co/api/daily_papers", timeout=30)
        resp.raise_for_status()
        papers = resp.json()
        for p in papers[:40]:  # top 40 (pool takes 30; headroom for MIN_SUMMARY_CHARS drops)
            paper = p.get("paper", {})
            paper_id = paper.get("id", "")
            items.append(make_item(
                category="papers",
                title=paper.get("title", ""),
                url=f"https://huggingface.co/papers/{paper_id}",
                summary=paper.get("summary", ""),
                source="HuggingFace Papers",
                published=paper.get("publishedAt") or paper.get("submittedOn"),
                metadata={"paper_id": paper_id},
            ))
    except Exception as e:
        print(f"[HuggingFace Papers] Error: {e}")
    return items


def fetch_github_trending():
    """Fetch trending repos from OSSInsight API (free, no auth)."""
    items = []
    try:
        resp = requests.get(
            "https://api.ossinsight.io/v1/trends/repos?period=past_24_hours",
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json().get("data", {}).get("rows", [])
        # Filter for AI/ML related repos by keywords
        for repo in rows:
            desc = (repo.get("description") or "").lower()
            name = (repo.get("repo_name") or "").lower()
            lang = (repo.get("primary_language") or "").lower()
            text = f"{desc} {name} {lang}"
            if any(kw in text for kw in AI_KEYWORDS):
                repo_name = repo.get("repo_name", "")
                items.append(make_item(
                    category="projects",
                    title=f"{repo_name} ⭐{repo.get('stars', 0)}",
                    url=f"https://github.com/{repo_name}",
                    summary=repo.get("description", "") or "No description",
                    source="GitHub Trending",
                    metadata={
                        "stars": repo.get("stars", 0),
                        "language": repo.get("primary_language"),
                    },
                ))
        items = items[:15]  # cap at 15
    except Exception as e:
        print(f"[GitHub Trending] Error: {e}")
    return items


BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
}


def fetch_github_trending_page(period="daily"):
    """Scrape github.com/trending for genuinely hot repos — higher quality and
    more recognizable than the OSSInsight 24h-star list. Keeps AI-related ones."""
    items = []
    try:
        resp = requests.get(
            f"https://github.com/trending?since={period}",
            headers=BROWSER_HEADERS, timeout=20,
        )
        resp.raise_for_status()
        rows = re.findall(r'<article class="Box-row">(.*?)</article>', resp.text, re.S)
        for row in rows:
            h2 = re.search(r'<h2.*?</h2>', row, re.S)
            if not h2:
                continue
            m = re.search(r'href="/([^"/]+/[^"/]+)"', h2.group(0))
            if not m:
                continue
            repo = m.group(1)
            dm = re.search(r'<p class="col-9[^"]*"[^>]*>(.*?)</p>', row, re.S)
            desc = clean_text(dm.group(1)) if dm else ""
            sm = re.search(r'([\d,]+)\s+stars today', row)
            today = sm.group(1) if sm else ""
            blob = f"{repo} {desc}".lower()
            if not any(kw in blob for kw in AI_KEYWORDS):
                continue
            note = f"（GitHub Trending · 今日 +{today} star）" if today else "（GitHub Trending）"
            items.append(make_item(
                category="projects",
                title=f"{repo} ⭐+{today}/天" if today else repo,
                url=f"https://github.com/{repo}",
                summary=f"{desc} {note}".strip(),
                source="GitHub Trending",
                metadata={"stars_today": today},
            ))
    except Exception as e:
        print(f"  [GitHub Trending page] Error: {e}")
    return items


def fetch_rss_feed(feed_url, source_name, max_items=10, cutoff_days=2):
    """Generic RSS feed fetcher."""
    items = []
    try:
        resp = requests.get(feed_url, headers=BROWSER_HEADERS, timeout=20)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        cutoff = datetime.now(timezone.utc) - timedelta(days=cutoff_days)
        # Scan a generous window so high-volume feeds (HF/OpenAI blogs with
        # hundreds of entries) still get correctly date-filtered.
        for entry in feed.entries[:max(max_items * 3, 40)]:
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                entry_date = datetime(*published[:6], tzinfo=timezone.utc)
                if entry_date < cutoff:
                    continue
                published_at = entry_date.isoformat()
            else:
                published_at = None
            items.append(make_item(
                category="news",
                title=entry.get("title", ""),
                url=entry.get("link", ""),
                summary=entry.get("summary", ""),
                source=source_name,
                published=published_at,
                metadata={"feed_url": feed_url},
            ))
            if len(items) >= max_items:
                break
    except Exception as e:
        print(f"  [{source_name}] Error: {e}")
    return items


def fetch_arxiv(max_items=12, cutoff_days=2):
    """Recent cs.AI/CL/CV/LG papers straight from arXiv — a non-curated safety
    net so important papers that don't make HuggingFace's daily top list still
    have a chance to surface."""
    items = []
    try:
        query = (
            "http://export.arxiv.org/api/query?"
            "search_query=cat:cs.CV+OR+cat:cs.CL+OR+cat:cs.LG+OR+cat:cs.AI"
            "&sortBy=submittedDate&sortOrder=descending&max_results=40"
        )
        resp = requests.get(query, headers=BROWSER_HEADERS, timeout=30)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        cutoff = datetime.now(timezone.utc) - timedelta(days=cutoff_days)
        for entry in feed.entries:
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            published_at = None
            if published:
                entry_date = datetime(*published[:6], tzinfo=timezone.utc)
                if entry_date < cutoff:
                    continue
                published_at = entry_date.isoformat()
            items.append(make_item(
                category="papers",
                title=entry.get("title", ""),
                url=entry.get("link", ""),
                summary=entry.get("summary", ""),
                source="arXiv",
                published=published_at,
            ))
            if len(items) >= max_items:
                break
    except Exception as e:
        print(f"  [arXiv] Error: {e}")
    return items


def fetch_hacker_news():
    """Fetch top AI stories from Hacker News via Algolia API."""
    items = []
    try:
        from datetime import datetime, timezone, timedelta
        since = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())
        # Pull recent high-point stories (no text query — a multi-term query is
        # ANDed and returns almost nothing); the AI_KEYWORDS title filter below
        # narrows it. The relevance /search endpoint now 400s, so use by-date.
        url = (
            f"https://hn.algolia.com/api/v1/search_by_date"
            f"?tags=story"
            f"&numericFilters=created_at_i>{since},points>10"
            f"&hitsPerPage=50"
        )
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        hits = resp.json().get("hits", [])

        for hit in hits:
            title = hit.get("title", "")
            # Filter for AI-relevant titles
            if not any(kw in title.lower() for kw in AI_KEYWORDS):
                continue
            items.append(make_item(
                category="news",
                title=title,
                url=hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                summary=f"HN points: {hit.get('points', 0)}, comments: {hit.get('num_comments', 0)}",
                source="Hacker News",
                published=hit.get("created_at"),
            ))
        items = items[:15]
    except Exception as e:
        print(f"  [Hacker News] Error: {e}")
    return items


# Public RSSHub instances, tried in order (the first that returns items wins).
# Public instances are individually flaky, so the fallback list keeps the
# rsshub-backed sources (36Kr, SSPAI) resilient.
RSSHUB_INSTANCES = [
    "https://rsshub.rssforever.com",
    "https://hub.slarker.me",
    "https://rsshub.app",
]


def rsshub(path):
    """Candidate URLs for an rsshub route across all configured instances."""
    return [instance + path for instance in RSSHUB_INSTANCES]


# RSS sources format: (url_or_url_list, name, max_items, cutoff_days)
# url may be a list of fallback URLs; the first that yields items is used.
# cutoff_days=3 for daily news, 7-14 for less frequent newsletters/blogs.
RSS_SOURCES = [
    # ── 中文 AI 媒体 ─────────────────────────────────────────────────────────
    (rsshub("/36kr/search/articles/ai"),                             "36Kr AI",    10, 3),
    ("https://www.qbitai.com/rss",                                   "量子位",     10, 3),
    ("https://www.infoq.cn/feed.xml",                                "InfoQ 中文",  8,  3),
    (rsshub("/sspai/tag/AI"),                                        "SSPAI AI",   8,  3),

    # ── 英文科技媒体 ─────────────────────────────────────────────────────────
    ("https://venturebeat.com/feed/",                                "VentureBeat",       8, 3),
    ("https://techcrunch.com/category/artificial-intelligence/feed/","TechCrunch AI",    10, 3),
    ("https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "The Verge AI",10, 3),
    ("https://www.wired.com/feed/tag/ai/latest/rss",                 "Wired AI",          8, 3),
    ("https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss", "IEEE Spectrum AI", 8, 3),
    ("https://feeds.arstechnica.com/arstechnica/technology-lab",     "Ars Technica AI",   8, 3),

    # ── AI 垂直媒体 / 个人博客（模型与研究发布的第一落点）────────────────────
    ("https://the-decoder.com/feed/",                                "The Decoder",   12, 3),
    ("https://www.marktechpost.com/feed/",                           "MarkTechPost",  12, 3),
    ("https://simonwillison.net/atom/everything/",                   "Simon Willison", 8, 4),

    # ── 官方实验室博客（低频，回溯更久以免错过发布）──────────────────────────
    ("https://huggingface.co/blog/feed.xml",                         "Hugging Face Blog", 6, 7),
    ("https://deepmind.google/blog/rss.xml",                         "Google DeepMind",   6, 10),
    ("https://blog.google/technology/ai/rss/",                       "Google AI Blog",    6, 10),
    ("https://openai.com/news/rss.xml",                              "OpenAI",            6, 10),

    # ── AI 圈讨论聚合（含 Twitter/X 大佬发言，由聚合源每天消化）──────────────
    ("https://news.smol.ai/rss.xml",                                 "smol.ai AINews",    5, 3),
    ("https://tldr.tech/api/rss/ai",                                 "TLDR AI",           6, 3),

    # ── 社区（best-effort，云端 IP 可能被限流，失败自动跳过）─────────────────
    ("https://www.reddit.com/r/LocalLLaMA/top/.rss?t=day",           "r/LocalLLaMA",      8, 2),

    # ── AI 研究者 Newsletter（更新较疏，回溯 14 天）──────────────────────────
    ("https://www.oneusefulthing.org/feed",   "One Useful Thing (Mollick)", 5, 14),
    ("https://www.lennysnewsletter.com/feed", "Lenny's Newsletter",         5, 14),
]


def fetch_all():
    """Fetch from all sources, return categorized data."""
    print("Fetching HuggingFace Papers...")
    papers = fetch_huggingface_papers()
    print(f"  Got {len(papers)} papers")

    print("Fetching arXiv (cs.AI/CL/CV/LG)...")
    arxiv_items = fetch_arxiv()
    print(f"  Got {len(arxiv_items)} arXiv papers")
    papers.extend(arxiv_items)

    print("Fetching GitHub Trending (official page)...")
    trending_page = fetch_github_trending_page()
    print(f"  Got {len(trending_page)} trending repos")

    print("Fetching GitHub Trending (OSSInsight)...")
    oss_projects = fetch_github_trending()
    print(f"  Got {len(oss_projects)} projects")

    # Official trending first (higher quality), OSSInsight as backup/supplement
    projects = trending_page + oss_projects

    print("Fetching Hacker News...")
    hn_items = fetch_hacker_news()
    print(f"  Got {len(hn_items)} items")

    print("Fetching RSS feeds...")
    news = list(hn_items)
    for url, name, max_items, cutoff_days in RSS_SOURCES:
        candidates = url if isinstance(url, (list, tuple)) else [url]
        feed_items = []
        for candidate in candidates:
            feed_items = fetch_rss_feed(candidate, name, max_items=max_items, cutoff_days=cutoff_days)
            if feed_items:  # first instance that works wins
                break
        print(f"  [{name}] Got {len(feed_items)} items")
        news.extend(feed_items)

    data = {
        "papers": dedupe_items(papers),
        "projects": dedupe_items(projects),
        "news": dedupe_items(news),
    }

    deduped_total = sum(len(v) for v in data.values())
    raw_total = len(papers) + len(projects) + len(news)
    print(f"Deduped {raw_total} raw items to {deduped_total} unique items")
    return data
