"""Main scraper: fetch all sources, extract events, update cache and result."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sources import SOURCES, PARSERS
from fetch import (
    OUTPUT_DIR, load_cache, save_cache, load_detail_cache, save_detail_cache,
    content_hash, fetch_page, extract_list_text, fetch_detail_text, save_file,
    DETAIL_TTL_DAYS,
)
from extract import create_client, extract_events, enrich_events


def process_source(client, src, cache, detail_cache, run_dir):
    """Process a single source. Returns (events, text_length) or None on failure."""
    name = src["name"]
    slug = re.sub(r"[^\w]", "_", name)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"Fetching {name}... ", end="", flush=True)

    # --- Fetch ---
    try:
        if "api" in src:
            text = fetch_page(src["api"])
        elif "parser" in src:
            text = fetch_page(src["url"])
        else:
            raw_html = fetch_page(src["url"])
            text = extract_list_text(raw_html, src)
    except Exception as e:
        print(f"FETCH ERROR: {e}", end="")
        cached = cache.get(name)
        if cached and cached.get("events"):
            print(f" -> using cached {len(cached['events'])} events")
            return cached["events"], 0
        print(" -> no cache available, skipping")
        return None

    if not text:
        print("no content extracted, skipping")
        return None

    save_file(run_dir / f"{slug}.txt", text)
    print(f"  {len(text)} chars")

    # --- Check cache ---
    h = content_hash(text)
    cached = cache.get(name)
    if cached and cached.get("content_hash") == h:
        events = cached["events"]
        selector = src.get("detail_selector")
        if selector and events:
            expired = []
            for e in events:
                u = e.get("url")
                if not u:
                    continue
                dc = detail_cache.get(u)
                if not dc or not dc.get("fetched_at"):
                    expired.append(u)
                else:
                    age = datetime.now(timezone.utc) - datetime.fromisoformat(dc["fetched_at"])
                    if age >= timedelta(days=DETAIL_TTL_DAYS):
                        expired.append(u)
            if expired:
                print(f"  -> {len(expired)} detail(s) expired, refreshing...", end="", flush=True)
                detail_texts = {}
                for e in events:
                    u = e.get("url")
                    if not u:
                        continue
                    dt = fetch_detail_text(u, selector, detail_cache)
                    if dt:
                        detail_texts[u] = dt
                events = PARSERS[src["parser"]](text, today) if "parser" in src else events
                print(f" enriching...", end="", flush=True)
                events = enrich_events(client, events, detail_texts, detail_cache)
                print(f" done")
                cache[name] = {
                    "content_hash": h,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "events": events,
                }
            else:
                print(f"  -> cache hit ({cached['fetched_at']})")
        else:
            print(f"  -> cache hit ({cached['fetched_at']})")
        return events, len(text)

    # --- Extract ---
    if "parser" in src:
        parser_fn = PARSERS[src["parser"]]
        events = parser_fn(text, today)
        print(f"  -> parsed {len(events)} events", end="", flush=True)

        detail_texts = None
        selector = src.get("detail_selector")
        if selector:
            detail_texts = {}
            fetched, cached_count = 0, 0
            for e in events:
                url = e.get("url")
                if not url:
                    continue
                was_cached = url in detail_cache
                dt = fetch_detail_text(url, selector, detail_cache)
                if dt:
                    detail_texts[url] = dt
                    if was_cached:
                        cached_count += 1
                    else:
                        fetched += 1
            print(f", details: {fetched} fetched / {cached_count} cached", end="", flush=True)

        print(f", enriching...", end="", flush=True)
        events = enrich_events(client, events, detail_texts, detail_cache)
        print(f" done")
    else:
        events = extract_events(client, name, src["url"], text)
        print(f"  -> {len(events)} events extracted", end="", flush=True)

        selector = src.get("detail_selector")
        if selector and events:
            detail_texts = {}
            for e in events:
                url = e.get("url")
                if not url:
                    continue
                dt = fetch_detail_text(url, selector, detail_cache)
                if dt:
                    detail_texts[url] = dt
            if detail_texts:
                print(f", {len(detail_texts)} details fetched, enriching...", end="", flush=True)
                events = enrich_events(client, events, detail_texts, detail_cache)

        print(f" done")

    # --- Fallback: fill missing URL with source URL ---
    for e in events:
        if not e.get("url"):
            e["url"] = src["url"]

    # --- Cache fallback: don't overwrite good cache with empty result ---
    if not events and cached and cached.get("events"):
        print(f"  [FALLBACK] keeping {len(cached['events'])} cached events")
        events = cached["events"]
        # Don't update cache — next run will retry extraction
    else:
        cache[name] = {
            "content_hash": h,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "events": events,
        }

    return events, len(text)


def main():
    client = create_client()

    cache = load_cache()
    detail_cache = load_detail_cache()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = OUTPUT_DIR / ts

    result = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sources": [],
    }

    for src in SOURCES:
        out = process_source(client, src, cache, detail_cache, run_dir)
        if out is None:
            continue
        events, text_len = out
        result["sources"].append({
            "name": src["name"],
            "url": src["url"],
            "content_length": text_len,
            "events": events,
        })
        save_cache(cache)
        save_detail_cache(detail_cache)

    json_str = json.dumps(result, ensure_ascii=False, indent=2)
    save_file(run_dir / "result.json", json_str)
    save_file(Path("result.json"), json_str)

    print(f"\nDone: {len(result['sources'])} sources, {sum(len(s['events']) for s in result['sources'])} events")


if __name__ == "__main__":
    main()
