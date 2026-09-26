"""
Voice broadcast: turn a daily digest into a natural spoken script (DeepSeek)
and synthesize it to an MP3 with edge-tts (free Microsoft neural voices).

Designed for commute listening: the MP3 is hosted under docs/audio/ and also
surfaced via a podcast RSS feed, so listeners can subscribe in any podcast app.
"""

import asyncio
import os
import re

import edge_tts

from generate_site import parse_digest, WEEKDAYS


# News-anchor style zh voice; override with AUDIO_VOICE if desired.
DEFAULT_VOICE = "zh-CN-YunyangNeural"

NARRATION_SYSTEM = """你是一档 AI 行业每日播客的主播，要把书面简报改写成自然、口语化的播报稿，供听众在通勤路上收听。

要求：
1. 纯口语，像电台主播在讲话，不要任何 Markdown、星号、链接、"来源"、"重要性 五星" 这类书面元素。
2. 开头一句问候并报出日期，再用一两句话概括今天的看点；中间按主题自然串讲重点条目（不要逐条机械念，可合并同类、加入过渡词）；结尾用"今日观察"作为总结性的收尾，并道别（如"我们明天见"）。
3. 专有名词（公司名、模型名等）保留英文原文，其余用中文；数字、英文缩写要写成适合朗读的形式。
4. 控制在 600-1000 字，节奏明快，信息密度适中，适合 4-6 分钟收听。
5. 只输出播报稿正文，不要加任何标题、小节名或解释。"""


def _deepseek_script(zh_markdown: str, date_str: str) -> str | None:
    """Ask DeepSeek to rewrite the digest into a spoken script. None on failure."""
    api_key = os.environ.get("API_KEY")
    if not api_key:
        print("[audio] API_KEY not set; using deterministic script.")
        return None

    import requests

    base_url = os.environ.get("API_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("API_MODEL", "deepseek-chat")
    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": NARRATION_SYSTEM},
                    {"role": "user", "content":
                        f"今天是 {date_str}。请把下面的简报改写成播报稿：\n\n{zh_markdown}"},
                ],
                "temperature": 0.6,
                "max_tokens": 3000,
            },
            timeout=180,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        return text or None
    except Exception as e:  # network / API hiccup → fall back, never block audio
        print(f"[audio] DeepSeek narration failed ({e}); using deterministic script.")
        return None


def _weekday(date_str: str) -> str:
    from datetime import datetime
    try:
        return WEEKDAYS[datetime.strptime(date_str, "%Y-%m-%d").weekday()]
    except Exception:
        return ""


# The narrator targets 600-1000 characters. Reading every item aloud would now
# be ~8000 (bodies are 400 chars each) — a 20-minute episode — so the fallback
# reads the headline picks only, trimmed.
FALLBACK_ITEMS_PER_CAT = 2
FALLBACK_DESC_CHARS = 150
FALLBACK_OBSERVATION_CHARS = 300


def _fallback_script(zh_markdown: str, date_str: str) -> str:
    """Deterministic spoken script built from the parsed digest (no LLM)."""
    digest = parse_digest(zh_markdown)
    parts = [f"AI 每日简报，{_weekday(date_str)}，{date_str}。以下是今天的重点。"]
    for cat in digest["categories"]:
        # cat["items"] is the headline picks; alternates are deliberately skipped.
        items = cat["items"][:FALLBACK_ITEMS_PER_CAT]
        if not items:
            continue
        parts.append(f"{cat['name']}。")
        for it in items:
            line = it["title"]
            if it["desc"]:
                line += "。" + it["desc"][:FALLBACK_DESC_CHARS]
            parts.append(line.rstrip("。") + "。")
    if digest["observation"]:
        parts.append("今日观察。" + digest["observation"][:FALLBACK_OBSERVATION_CHARS])
    parts.append("以上就是今天的 AI 简报，我们明天见。")
    return "\n".join(parts)


def _clean_for_tts(text: str) -> str:
    """Strip any stray markdown/symbols that would be read awkwardly."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)   # [name](url) -> name
    text = re.sub(r"[*#`>_]+", "", text)                    # md emphasis/headers
    text = re.sub(r"[★☆]+", "", text)                       # stray stars
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text


async def _synthesize(text: str, out_path: str, voice: str) -> None:
    await edge_tts.Communicate(text, voice).save(out_path)


def generate_audio(zh_markdown: str, date_str: str, audio_dir):
    """Generate {date}.mp3 (+ {date}.txt show-notes script) in audio_dir.

    Returns (mp3_path, script_text) or (None, None) if synthesis failed.
    """
    from pathlib import Path
    audio_dir = Path(audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)

    script = _deepseek_script(zh_markdown, date_str) or _fallback_script(zh_markdown, date_str)
    script = _clean_for_tts(script)
    if not script:
        print("[audio] Empty script; skipping audio.")
        return None, None

    voice = os.environ.get("AUDIO_VOICE", DEFAULT_VOICE)
    mp3_path = audio_dir / f"{date_str}.mp3"
    print(f"[audio] Synthesizing {mp3_path.name} with {voice} ({len(script)} chars)...")
    try:
        asyncio.run(_synthesize(script, str(mp3_path), voice))
    except Exception as e:
        print(f"[audio] edge-tts synthesis failed: {e}")
        return None, None

    # Save the script as show notes / transcript.
    (audio_dir / f"{date_str}.txt").write_text(script, encoding="utf-8")
    size = mp3_path.stat().st_size
    print(f"[audio] Saved {mp3_path.name} ({size // 1024} KB)")
    return mp3_path, script


def prune_old_audio(audio_dir, keep: int = 15) -> None:
    """Keep only the `keep` most recent episodes; delete older mp3 + txt.

    Bounds repo size (~3 MB/day). Date-named files sort chronologically, so the
    tail is the newest. Older day pages just lose their player — text content is
    untouched (generate_site only shows a player when the mp3 still exists)."""
    from pathlib import Path
    audio_dir = Path(audio_dir)
    if not audio_dir.exists():
        return
    mp3s = sorted(audio_dir.glob("*.mp3"))
    for mp3 in mp3s[:-keep] if len(mp3s) > keep else []:
        mp3.unlink(missing_ok=True)
        mp3.with_suffix(".txt").unlink(missing_ok=True)
        print(f"[audio] pruned old episode {mp3.stem}")
