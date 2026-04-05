"""Run scraper for a single source by name. Clears its cache before running."""
import json
import sys
from pathlib import Path

from scraper import (
    SOURCES, OUTPUT_DIR, CACHE_FILE, DETAIL_CACHE_FILE,
    load_cache, save_cache, load_detail_cache, save_detail_cache,
    fetch_page, clean_html, content_hash, fetch_detail_text,
    extract_events, enrich_events, save_file, PARSERS,
    BeautifulSoup, re, anthropic, os,
    datetime, timezone, hashlib,
)


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

    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        print("ERROR: Set MINIMAX_API_KEY environment variable")
        return

    client = anthropic.Anthropic(
        api_key=api_key,
        base_url="https://api.minimaxi.com/anthropic",
    )

    cache = load_cache()
    detail_cache = load_detail_cache()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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

    slug = re.sub(r"[^\w]", "_", src["name"])
    print(f"Fetching {src['name']}... ", end="", flush=True)

    try:
        if "api" in src:
            text = fetch_page(src["api"])
        else:
            raw_html = fetch_page(src["url"])
            list_sel = src.get("list_selector")
            if list_sel:
                soup = BeautifulSoup(raw_html, "html.parser")
                items = soup.select(list_sel)
                link_sel = src.get("link_selector")
                base_url = "/".join(src["url"].split("/", 3)[:3])
                parts = []
                for el in items:
                    el_text = re.sub(r"\n{3,}", "\n\n", el.get_text(separator="\n").strip())
                    links = []
                    if link_sel == "self":
                        if el.name == "a" and el.get("href"):
                            links = [el]
                    elif link_sel:
                        links = el.select(link_sel)
                    for a in links:
                        if not a.get("href"):
                            continue
                        href = a["href"]
                        if href.startswith("/"):
                            href = base_url + href
                        elif not href.startswith("http"):
                            href = src["url"].rsplit("/", 1)[0] + "/" + href
                        el_text = f"[URL: {href}]\n{el_text}"
                    parts.append(el_text)
                parts.sort()
                text = "\n\n".join(parts) if parts else None
            else:
                text = clean_html(raw_html)
    except Exception as e:
        print(f"FETCH ERROR: {e}")
        return

    if not text:
        print("no content extracted")
        return

    save_file(run_dir / f"{slug}.txt", text)
    print(f"  {len(text)} chars")

    if "parser" in src:
        parser_fn = PARSERS[src["parser"]]
        events = parser_fn(text, today)
        print(f"  parsed {len(events)} events", end="", flush=True)

        detail_texts = None
        selector = src.get("detail_selector")
        if selector:
            detail_texts = {}
            for e in events:
                url = e.get("url")
                if not url:
                    continue
                dt = fetch_detail_text(url, selector, detail_cache)
                if dt:
                    detail_texts[url] = dt
            print(f", {len(detail_texts)} details fetched", end="", flush=True)

        print(f", enriching...", end="", flush=True)
        events = enrich_events(client, events, detail_texts)
        print(f" done")
    else:
        events = extract_events(client, src["name"], src["url"], text)
        print(f"  → {len(events)} events extracted", end="", flush=True)

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
                events = enrich_events(client, events, detail_texts)

        print(f" done")

    # Update cache
    h = content_hash(text)
    cache[name] = {
        "content_hash": h,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "events": events,
    }
    save_cache(cache)
    save_detail_cache(detail_cache)

    # Update result.json
    result_path = Path("result.json")
    if result_path.exists():
        result = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        result = {"fetched_at": datetime.now(timezone.utc).isoformat(), "sources": []}

    # Replace or append this source
    result["sources"] = [s for s in result["sources"] if s["name"] != name]
    result["sources"].append({
        "name": src["name"],
        "url": src["url"],
        "content_length": len(text),
        "events": events,
    })
    save_file(result_path, json.dumps(result, ensure_ascii=False, indent=2))

    from generate_ics import generate_ics
    save_file(Path("calendar.ics"), generate_ics(result))

    print(f"\nResults: {len(events)} events")
    for e in events:
        print(f"  {e.get('title', '')[:40]} | {e.get('url', '')}")


if __name__ == "__main__":
    main()
