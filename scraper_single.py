"""Run scraper for a single source by name. Clears its cache before running."""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

from sources import SOURCES
from fetch import OUTPUT_DIR, load_cache, save_cache, load_detail_cache, save_detail_cache, save_file
from extract import create_client
from scraper import process_source


def main():
    if len(sys.argv) < 2:
        print("Usage: scraper_single.py <source_name>")
        print("Available sources:")
        for s in SOURCES:
            print(f"  {s['name']}")
        return

    name = sys.argv[1]
    src = next((s for s in SOURCES if s["name"] == name), None)
    if not src:
        print(f"Source not found: {name}")
        return

    client = create_client()
    cache = load_cache()
    detail_cache = load_detail_cache()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = OUTPUT_DIR / ts

    # Clear cache for this source
    if name in cache:
        del cache[name]
        print(f"Cleared cache for {name}")

    # Clear detail cache for URLs matching this source
    source_urls = [k for k in detail_cache if src["url"].split("/")[2] in k]
    for k in source_urls:
        del detail_cache[k]
    if source_urls:
        print(f"Cleared {len(source_urls)} detail cache entries")

    out = process_source(client, src, cache, detail_cache, run_dir)
    if out is None:
        return
    events, text_len, extract_meta = out

    save_cache(cache)
    save_detail_cache(detail_cache)

    # Update result.json
    result_path = Path("result.json")
    if result_path.exists():
        result = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        result = {"fetched_at": datetime.now(timezone.utc).isoformat(), "sources": []}

    result["sources"] = [s for s in result["sources"] if s["name"] != name]
    result["sources"].append({
        "name": src["name"],
        "url": src["url"],
        "content_length": text_len,
        "extract_meta": extract_meta,
        "events": events,
    })
    save_file(result_path, json.dumps(result, ensure_ascii=False, indent=2))

    print(f"\nResults: {len(events)} events")
    for e in events:
        print(f"  {e.get('title', '')[:40]} | {e.get('url', '')}")


if __name__ == "__main__":
    main()
