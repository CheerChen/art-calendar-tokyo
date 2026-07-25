"""Main scraper: fetch all sources, extract events, update cache and result."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from event_schema import EVENT_SCHEMA_VERSION, normalize_events
from sources import (
    ENRICH_PROMPT_BASIC,
    ENRICH_PROMPT_DETAIL,
    PARSERS,
    PIPELINE_VERSION,
    SOURCES,
    SYSTEM_PROMPT_TEMPLATE,
)
from fetch import (
    OUTPUT_DIR, load_cache, save_cache, load_detail_cache, save_detail_cache,
    content_hash, fetch_page, extract_list, fetch_detail_text, save_file,
    report_event_stats, DETAIL_TTL_DAYS,
)
from extract import (
    build_enrich_input,
    create_client,
    enrich_events,
    extract_events,
    get_model_candidates,
    get_selected_model,
)
from venue_matching import build_matchers, load_venues, match_venue


def _empty_meta() -> dict:
    return {"item_count": None, "empty_url": 0, "empty_image": 0, "warnings": []}


def _content_fingerprint(llm_input: str) -> str:
    """Hash input together with every configured output-affecting version."""
    payload = {
        "pipeline_version": PIPELINE_VERSION,
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "model_candidates": get_model_candidates(),
        "prompts": [
            SYSTEM_PROMPT_TEMPLATE,
            ENRICH_PROMPT_BASIC,
            ENRICH_PROMPT_DETAIL,
        ],
        "input": llm_input,
    }
    return content_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _cache_entry(content_fingerprint: str, events: list, client) -> dict:
    return {
        "content_hash": content_fingerprint,
        "pipeline_version": PIPELINE_VERSION,
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "model_candidates": get_model_candidates(),
        "model_used": get_selected_model(client),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "events": events,
    }


def _collect_detail_texts(events: list, selector: str | None, detail_cache: dict) -> tuple[dict, int, int]:
    """Fetch available detail text once and report refreshed/cached counts."""
    if not selector:
        return {}, 0, 0

    detail_texts = {}
    fetched = 0
    cached = 0
    for event in events:
        url = event.get("url")
        if not url:
            continue
        before = (detail_cache.get(url) or {}).get("fetched_at")
        detail = fetch_detail_text(url, selector, detail_cache)
        if not detail:
            continue
        detail_texts[url] = detail
        after = (detail_cache.get(url) or {}).get("fetched_at")
        if before and before == after:
            cached += 1
        else:
            fetched += 1
    return detail_texts, fetched, cached


def _enrich_source_events(
    client,
    events: list,
    selector: str | None,
    detail_cache: dict,
    *,
    enrich_without_details: bool,
) -> list:
    """Share detail fetching and enrichment while preserving caller policy."""
    detail_texts, fetched, cached = _collect_detail_texts(events, selector, detail_cache)
    if selector:
        print(f"  details: {fetched} fetched / {cached} cached,", end="", flush=True)
    if not detail_texts and not enrich_without_details:
        if selector:
            print(" LLM enrich skipped (no detail content)")
        return events
    print(" LLM enrich...", end="", flush=True)
    enriched = enrich_events(client, events, detail_texts or None, detail_cache)
    print(f" done ({len(enriched)} events)")
    return enriched


def _finalize_events(events: list, src: dict, extract_meta: dict, venue_matchers) -> list:
    """Validate the public schema and assign canonical venue keys."""
    events, schema_warnings = normalize_events(events, source_url=src["url"])
    warnings = extract_meta.setdefault("warnings", [])
    warnings.extend(f"SCHEMA {warning}" for warning in schema_warnings)

    canonicals, aliases = venue_matchers
    unmatched = set()
    for event in events:
        event["venue_key"] = match_venue(event.get("venue"), canonicals, aliases)
        if event.get("venue") and not event["venue_key"]:
            unmatched.add(event["venue"])
    for venue in sorted(unmatched):
        warnings.append(f"VENUE? no coordinates for: {venue}")
    return events


def process_source(client, src, cache, detail_cache, run_dir, venue_matchers):
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
            events = _finalize_events(cached["events"], src, extract_meta, venue_matchers)
            return events, 0, extract_meta
        print(" -> no cache available, skipping")
        return None

    if not text:
        warn = "; ".join(extract_meta.get("warnings") or []) or "empty extract"
        print(f"no content extracted ({warn})", end="")
        cached = cache.get(name)
        if cached and cached.get("events"):
            print(f" -> using cached {len(cached['events'])} events")
            events = _finalize_events(cached["events"], src, extract_meta, venue_matchers)
            return events, 0, extract_meta
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
        h = _content_fingerprint(llm_input)
        print(f"  {len(parsed_events)} parsed, {len(llm_input)} chars")
    else:
        llm_input = text
        h = _content_fingerprint(llm_input)
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
                if is_parser:
                    events = parsed_events
                events = _enrich_source_events(
                    client,
                    events,
                    selector,
                    detail_cache,
                    enrich_without_details=True,
                )
                events = _finalize_events(events, src, extract_meta, venue_matchers)
                cache[name] = _cache_entry(h, events, client)
                return events, len(llm_input), extract_meta
            else:
                print(f"  -> cache hit, LLM skipped (cached {cached['fetched_at']})")
        else:
            print(f"  -> cache hit, LLM skipped (cached {cached['fetched_at']})")
        events = _finalize_events(events, src, extract_meta, venue_matchers)
        return events, len(llm_input), extract_meta

    # --- Extract / Enrich ---
    if is_parser:
        events = parsed_events
        selector = src.get("detail_selector")
        events = _enrich_source_events(
            client,
            events,
            selector,
            detail_cache,
            enrich_without_details=True,
        )
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
            events = _enrich_source_events(
                client,
                events,
                selector,
                detail_cache,
                enrich_without_details=False,
            )

    events = _finalize_events(events, src, extract_meta, venue_matchers)

    # --- Cache fallback: don't overwrite good cache with empty result ---
    if not events and cached and cached.get("events"):
        reason = "; ".join(extract_meta.get("warnings") or []) or "0 events"
        print(f"  [FALLBACK] keeping {len(cached['events'])} cached events ({reason})")
        events = _finalize_events(cached["events"], src, extract_meta, venue_matchers)
        # Don't update cache — next run will retry extraction
    else:
        cache[name] = _cache_entry(h, events, client)

    return events, len(llm_input), extract_meta


def main():
    client = create_client()

    cache = load_cache()
    detail_cache = load_detail_cache()
    venues = load_venues()
    venue_matchers = build_matchers(venues)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = OUTPUT_DIR / ts

    result = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "sources": [],
    }

    for src in SOURCES:
        out = process_source(client, src, cache, detail_cache, run_dir, venue_matchers)
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
