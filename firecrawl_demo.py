"""Compare existing source extraction with Firecrawl on a single source."""
import argparse
import json
import os
import re
from typing import Any

import requests

from fetch import extract_list_text, fetch_page
from sources import SOURCES

FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare the repo's current extraction path with Firecrawl."
    )
    parser.add_argument(
        "source_name",
        help="Exact source name from sources.py",
    )
    parser.add_argument(
        "--run-llm",
        action="store_true",
        help="Also run extract_events() on both inputs if LLM credentials are configured.",
    )
    return parser.parse_args()


def get_source(source_name: str) -> dict[str, Any]:
    src = next((item for item in SOURCES if item["name"] == source_name), None)
    if not src:
        names = "\n".join(f"  - {item['name']}" for item in SOURCES)
        raise SystemExit(f"Unknown source: {source_name}\nAvailable sources:\n{names}")
    if "api" in src or "parser" in src:
        raise SystemExit(
            "This demo is intended for HTML list-page sources only. "
            "Choose a source without 'api' or 'parser'."
        )
    return src


def firecrawl_scrape(url: str) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = requests.post(
        FIRECRAWL_SCRAPE_URL,
        headers=headers,
        json={"url": url, "formats": ["markdown"]},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    return payload["data"]


def preview(text: str, limit: int = 240) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit]


def link_count(text: str) -> int:
    return len(set(re.findall(r"https?://[^\s\])>\"']+", text)))


def print_section(title: str):
    print(f"\n=== {title} ===")


def main():
    args = parse_args()
    src = get_source(args.source_name)

    raw_html = fetch_page(src["url"])
    existing_text = extract_list_text(raw_html, src) or ""

    firecrawl_data = firecrawl_scrape(src["url"])
    firecrawl_markdown = firecrawl_data.get("markdown", "")

    print_section("Source")
    print(src["name"])
    print(src["url"])

    print_section("Existing Pipeline")
    print(f"chars: {len(existing_text)}")
    print(f"links: {link_count(existing_text)}")
    print(f"preview: {preview(existing_text)}")

    print_section("Firecrawl")
    print(f"chars: {len(firecrawl_markdown)}")
    print(f"links: {link_count(firecrawl_markdown)}")
    print(f"credits_used: {firecrawl_data.get('metadata', {}).get('creditsUsed')}")
    print(f"preview: {preview(firecrawl_markdown)}")

    if args.run_llm:
        from extract import create_client, extract_events

        client = create_client()
        existing_events = extract_events(client, src["name"], src["url"], existing_text)
        firecrawl_events = extract_events(client, src["name"], src["url"], firecrawl_markdown)

        print_section("LLM Result Comparison")
        print(f"existing_events: {len(existing_events)}")
        print(f"firecrawl_events: {len(firecrawl_events)}")

        print_section("Existing Event Titles")
        for event in existing_events[:10]:
            print(f"- {event.get('title', '')}")

        print_section("Firecrawl Event Titles")
        for event in firecrawl_events[:10]:
            print(f"- {event.get('title', '')}")


if __name__ == "__main__":
    main()
