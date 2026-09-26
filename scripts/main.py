"""
AI Daily Digest - Main entry point.
Fetches AI news from multiple sources, summarizes with AI, and saves outputs.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

from sources import fetch_all
from summarize import summarize, LAST_REPORT
from generate_site import generate_site
from audio import generate_audio, prune_old_audio


def _raw_payload(date_str, today, data):
    """The committed daily trace: counts, the LLM's selection, its warnings.

    The 500-char summary projection is taken from `data` here, at each write
    site, rather than passed in as a snapshot captured earlier. This file is
    written twice (before and after step 3); deriving `items` fresh means a
    future summarizer that mutated `data` in place could not leave step 3.5
    writing stale entries.
    """
    return {
        "date": date_str,
        "generated_at": today.isoformat(),
        "counts": {key: len(value) for key, value in data.items()},
        "selection": LAST_REPORT.get("selection", {}),
        "pool": LAST_REPORT.get("pool", {}),
        "warnings": LAST_REPORT.get("warnings", []),
        "items": {
            key: [{**item, "summary": (item.get("summary") or "")[:500]} for item in value]
            for key, value in data.items()
        },
    }


def send_to_buttondown(subject: str, markdown: str) -> None:
    """Publish the digest as a Buttondown email (sends to all subscribers)."""
    api_key = os.environ.get("BUTTONDOWN_API_KEY")
    if not api_key:
        print("[Buttondown] BUTTONDOWN_API_KEY not set, skipping.")
        return

    resp = requests.post(
        "https://api.buttondown.email/v1/emails",
        headers={
            "Authorization": f"Token {api_key}",
            "X-Buttondown-Live-Dangerously": "true",
        },
        json={
            "subject": subject,
            "body": markdown,
            "status": "about_to_send",  # queues for immediate send
            "email_type": "public",    # visible in archive
        },
        timeout=30,
    )
    if resp.status_code in (200, 201):
        print(f"[Buttondown] Sent! Email id: {resp.json().get('id')}")
    else:
        print(f"[Buttondown] Failed {resp.status_code}: {resp.text[:200]}")


def main():
    # Use Beijing time for date
    beijing_tz = timezone(timedelta(hours=8))
    today = datetime.now(beijing_tz)
    date_str = today.strftime("%Y-%m-%d")

    print(f"=== AI Daily Digest for {date_str} ===\n")

    # Step 1: Fetch data from all sources
    print("[Step 1] Fetching data from sources...")
    data = fetch_all()

    total = sum(len(v) for v in data.values())
    print(f"\nTotal items fetched: {total}")

    if total == 0:
        print("No items fetched. Exiting.")
        sys.exit(0)

    # Step 2: Save raw fetched data for traceability.
    # The pipeline now fetches up to 1500 chars per summary so the model has
    # enough material for a 400-char write-up, but the committed fixture keeps
    # the historical 500-char shape: these files run 130-150KB/day, nothing
    # reads them back, and growing them 3x buys nothing.
    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    raw_file = data_dir / f"{date_str}.raw.json"
    raw_file.write_text(
        json.dumps(_raw_payload(date_str, today, data),
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[Step 2] Saved raw data to {raw_file}")

    # Step 3: AI summarization
    print("\n[Step 3] Generating AI summary...")
    markdown, markdown_en = summarize(data, date_str)

    # Step 3.5: rewrite the raw trace with the summarizer's selection + warning
    # ledger. Written separately from step 2 so a run that dies in step 3 still
    # leaves the raw data behind — which is exactly when it is worth having.
    raw_file.write_text(
        json.dumps(_raw_payload(date_str, today, data),
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Step 4: Save daily markdown (zh + en)
    output_dir = Path(__file__).parent.parent / "daily"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / f"{date_str}.md"

    output_file.write_text(markdown, encoding="utf-8")
    print(f"\n[Step 4] Saved to {output_file}")

    if markdown_en:
        output_file_en = output_dir / f"{date_str}.en.md"
        output_file_en.write_text(markdown_en, encoding="utf-8")
        print(f"          Saved English to {output_file_en}")
    else:
        print("          No English version generated.")

    # Step 5: Synthesize voice broadcast (MP3 for commute listening)
    print("\n[Step 5] Generating voice broadcast...")
    audio_dir = Path(__file__).parent.parent / "docs" / "audio"
    try:
        generate_audio(markdown, date_str, audio_dir)
        prune_old_audio(audio_dir, keep=15)  # keep ~half a month of episodes
    except Exception as e:
        print(f"  Audio generation skipped: {e}")

    # Step 6: Rebuild static site (picks up today's audio + podcast feed)
    print("\n[Step 6] Rebuilding static site...")
    generate_site(root=Path(__file__).parent.parent)
    print("  Site rebuilt → docs/")

    # Step 7: Send to Buttondown subscribers
    print("\n[Step 7] Sending to Buttondown subscribers...")
    subject = f"AI Daily Digest · {date_str}"
    send_to_buttondown(subject, markdown)

    print("Done!")


if __name__ == "__main__":
    main()
