"""Main scraper: fetch all sources, extract events, update cache and result."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sources import SOURCES, PARSERS
from fetch import (
    OUTPUT_DIR, load_cache, save_cache, load_detail_cache, save_detail_cache,
    content_hash, fetch_page, extract_list, fetch_detail_text, save_file,
    report_event_stats, DETAIL_TTL_DAYS,
)
from extract import create_client, extract_events, enrich_events, build_enrich_input


def _empty_meta() -> dict:
    return {"item_count": None, "empty_url": 0, "empty_image": 0, "warnings": []}


def process_source(client, src, cache, detail_cache, run_dir):
    """Process a single source.

    Returns (events, text_length, extract_meta) or None on hard failure.
    extract_meta carries never-silent diagnostics (DRIFT?/SPA?/field empties).
    """
    name = src["name"]
    slug = re.sub(r"[^\w]", "_", name)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    extract_meta = _empty_meta()
    print(f"Fetching {name}... ", end="", flush=True)

    # --- Fetch ---
    try:
        if "api" in src:
            text = fetch_page(src["api"])
        elif "parser" in src:
            text = fetch_page(src["url"])
        else:
            raw_html = fetch_page(src["url"])
            list_result = extract_list(raw_html, src)
            text = list_result.text
            extract_meta = list_result.to_meta()
    except Exception as e:
        print(f"FETCH ERROR: {e}", end="")
        cached = cache.get(name)
        if cached and cached.get("events"):
            print(f" -> fetch failed, using cached {len(cached['events'])} events (LLM skipped)")
            return cached["events"], 0, extract_meta
        print(" -> no cache available, skipping")
        return None

    if not text:
        warn = "; ".join(extract_meta.get("warnings") or []) or "empty extract"
        print(f"no content extracted ({warn})", end="")
        cached = cache.get(name)
        if cached and cached.get("events"):
            print(f" -> using cached {len(cached['events'])} events")
            return cached["events"], 0, extract_meta
        print(" -> no cache available, skipping")
        return None

    # --- For parser sources, always parse first to compute LLM input ---
    is_parser = "parser" in src
    parsed_events = None
    if is_parser:
        parser_fn = PARSERS[src["parser"]]
        parsed_events = parser_fn(text, today)
        extract_meta = report_event_stats(name, parsed_events, kind="parser")

    # --- Build the content we hash to detect changes (NOT yet sent to the LLM) ---
    if is_parser:
        _, llm_input = build_enrich_input(parsed_events)
        h = content_hash(llm_input)
        print(f"  {len(parsed_events)} parsed, {len(llm_input)} chars")
    else:
        llm_input = text
        h = content_hash(llm_input)
        print(f"  {len(llm_input)} chars")

    save_file(run_dir / f"{slug}.txt", llm_input)

    # --- Check cache ---
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
                if is_parser:
                    events = parsed_events
                print(f" LLM enrich...", end="", flush=True)
                events = enrich_events(client, events, detail_texts, detail_cache)
                print(f" done ({len(events)} events)")
                cache[name] = {
                    "content_hash": h,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "events": events,
                }
            else:
                print(f"  -> cache hit, LLM skipped (cached {cached['fetched_at']})")
        else:
            print(f"  -> cache hit, LLM skipped (cached {cached['fetched_at']})")
        return events, len(llm_input), extract_meta

    # --- Extract / Enrich ---
    if is_parser:
        events = parsed_events
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
            print(f"  details: {fetched} fetched / {cached_count} cached,", end="", flush=True)

        print(f" LLM enrich...", end="", flush=True)
        events = enrich_events(client, events, detail_texts, detail_cache)
        print(f" done ({len(events)} events)")
    else:
        events = extract_events(client, name, src["url"], text)
        # Merge LLM-stage stats into meta (keep structural warnings from list extract).
        llm_meta = report_event_stats(name, events, kind="llm")
        extract_meta = {
            **extract_meta,
            "empty_url": llm_meta["empty_url"],
            "empty_image": llm_meta["empty_image"],
            "warnings": list(extract_meta.get("warnings") or []) + llm_meta["warnings"],
            "llm_event_count": llm_meta["item_count"],
        }

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
                print(f"  details: {len(detail_texts)} fetched, LLM enrich...", end="", flush=True)
                events = enrich_events(client, events, detail_texts, detail_cache)
                print(" done")


    # --- Fallback: fill missing URL with source URL ---
    for e in events:
        if not e.get("url"):
            e["url"] = src["url"]

    # --- Cache fallback: don't overwrite good cache with empty result ---
    if not events and cached and cached.get("events"):
        reason = "; ".join(extract_meta.get("warnings") or []) or "0 events"
        print(f"  [FALLBACK] keeping {len(cached['events'])} cached events ({reason})")
        events = cached["events"]
        # Don't update cache — next run will retry extraction
    else:
        cache[name] = {
            "content_hash": h,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "events": events,
        }

    return events, len(llm_input), extract_meta


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
        events, text_len, extract_meta = out
        result["sources"].append({
            "name": src["name"],
            "url": src["url"],
            "content_length": text_len,
            "extract_meta": extract_meta,
            "events": events,
        })
        save_cache(cache)
        save_detail_cache(detail_cache)

    json_str = json.dumps(result, ensure_ascii=False, indent=2)
    save_file(run_dir / "result.json", json_str)
    save_file(Path("result.json"), json_str)

    n_warn = sum(1 for s in result["sources"] if (s.get("extract_meta") or {}).get("warnings"))
    print(
        f"\nDone: {len(result['sources'])} sources, "
        f"{sum(len(s['events']) for s in result['sources'])} events"
        f"{f', {n_warn} source(s) with warnings' if n_warn else ''}"
    )


if __name__ == "__main__":
    main()
