import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import requests
import trafilatura
from bs4 import BeautifulSoup

SOURCES = [
    {
        "name": "武蔵野美術大学",
        "url": "https://oc.musabi.ac.jp/",
    },
    {
        "name": "多摩美術大学",
        "url": "https://www.tamabi.ac.jp/open-campus/",
    },
    {
        "name": "東京都現代美術館",
        "url": "https://www.mot-art-museum.jp/exhibitions/",
        "api": "https://www.mot-art-museum.jp/json/exhibitions/exhibitions.json",
        "parser": "mot",
        "detail_selector": ".c-mf-exhibitions-wrapper",
    },
    {
        "name": "国立新美術館",
        "url": "https://www.nact.jp/exhibition_special/",
        "api": "https://www.nact.jp/exhibition_special/exhibition_special_ca.json",
        "parser": "nact",
        "detail_selector": ".acc_list",
    },
    {
        "name": "国立西洋美術館",
        "url": "https://www.nmwa.go.jp/jp/exhibitions/current.html",
        "list_selector": "main#main.exb_index > section",
        "link_selector": "p.lnk1 a",
    },
    {
        "name": "上野の森美術館",
        "url": "https://www.ueno-mori.org/exhibitions/",
        "list_selector": "ul#ScheduleList > li",
        "link_selector": "a",
    },
    {
        "name": "東京都美術館",
        "url": "https://www.tobikan.jp/exhibition/index.html",
        "list_selector": "section.container.mt30.s-mt15 a.exhibition-item",
        "link_selector": "self",
    },
    {
        "name": "アーティゾン美術館",
        "url": "https://www.artizon.museum/exhibition/",
        "list_selector": "div.case",
        "link_selector": "a",
    },
    {
        "name": "森美術館",
        "url": "https://www.mori.art.museum/jp/exhibitions/index.html",
        "list_selector": "div[class*='category-']",
        "link_selector": "a",
    },
    {
        "name": "21_21 DESIGN SIGHT",
        "url": "https://www.2121designsight.jp/",
    },
    {
        "name": "東京国立近代美術館",
        "url": "https://www.momat.go.jp/exhibitions",
        "list_selector": "div.box-page-wrapper section.item",
        "link_selector": "a",
    },
    {
        "name": "寺田倉庫",
        "url": "https://warehouseofart.org/",
        "list_selector": "li.frontEventItem",
    },
    {
        "name": "横浜美術館",
        "url": "https://yokohama.art.museum/exhibition/",
    },
    {
        "name": "東京オペラシティアートギャラリー",
        "url": "https://www.operacity.jp/contents/exhibition/upcoming?lang=ja&ag_home=0",
    },
    {
        "name": "SOMPO美術館",
        "url": "https://www.sompo-museum.org/exhibitions/#now",
    },
    {
        "name": "TOKYO NODE",
        "url": "https://www.tokyonode.jp/events/index.html",
        "api": "https://www.tokyonode.jp/assets/json/tn_all_events.json",
        "parser": "tokyonode",
        "detail_selector": "section.section_e-gallery_before_info",
    },
]

SYSTEM_PROMPT_TEMPLATE = """You are a structured data extractor for Japanese art exhibitions and university events.
Given the text content of a web page, extract ALL in-person (対面/現地開催) events, exhibitions, and open campus activities.
Exclude any online-only events (オンライン, ウェビナー, Zoom, 配信).
常設展など常時開催の展示は除外する。ただしコレクション展は含めてよい。
明確な開催期間（start_date と end_date）がないイベント（毎月開催、随時開催、通年開催など）は除外する。

Today's date is {today}. Only include events that have NOT ended yet (end_date >= today, or start_date >= today if no end_date). Exclude all past events.

For each event, return:
- title (string): event name
- venue (string): 施設名＋展示室名（例：「国立西洋美術館 企画展示室B2F」）。施設名は必ず含めること。
- start_date (string): ISO format YYYY-MM-DD, or null if unclear
- end_date (string): ISO format YYYY-MM-DD, or null if unclear
- start_time (string): opening time in HH:MM format (24h), or null if not specified (treat as all-day event)
- end_time (string): closing time in HH:MM format (24h), or null if not specified
- reservation_required (boolean): true if advance booking/reservation is explicitly required, false otherwise
- url (string): detail page URL if found, otherwise null
- summary (string): 1-2 sentence description of the event content
- recommendation (string): based on the exhibition title, artist/theme reputation, and venue prestige, classify as one of:
  "must_see" — internationally significant exhibitions, major retrospectives of renowned artists, or blockbuster museum collaborations
  "recommended" — notable solo exhibitions, well-known artists, or topical/unique themes
  "normal" — group shows, public competitions (公募展), calligraphy/craft guild exhibitions, or insufficient information to judge

すべて日本語で回答してください。

Return ONLY a JSON array. No markdown fences, no explanation.
If zero events found, return []."""

ENRICH_PROMPT_BASIC = """Given a list of art exhibition titles and venues, add a short Japanese summary (1-2 sentences) and a recommendation classification for each.

recommendation values:
- "must_see" — internationally significant exhibitions, major retrospectives of renowned artists, or blockbuster museum collaborations
- "recommended" — notable solo exhibitions, well-known artists, or topical/unique themes
- "normal" — group shows, public competitions, or insufficient information to judge

すべて日本語で回答してください。

Return ONLY a JSON array with objects containing: "index" (int), "summary" (string), "recommendation" (string).
No markdown fences, no explanation."""

ENRICH_PROMPT_DETAIL = """Given a list of art exhibitions with their detail page content, extract structured information and provide summary + recommendation for each.

For each item, return:
- "index" (int): the original index
- "summary" (string): 1-2 sentence Japanese description based on the detail content
- "recommendation" (string): "must_see", "recommended", or "normal"
  must_see = internationally significant exhibitions, major retrospectives, blockbuster collaborations
  recommended = notable solo exhibitions, well-known artists, unique themes
  normal = group shows, public competitions, or insufficient info
- "venue" (string): 施設名＋展示室名 extracted from the detail, or null if not found
- "start_time" (string): opening time in HH:MM format, or null
- "end_time" (string): closing time in HH:MM format, or null
- "closed_days" (string): 休館日 info as-is from the page, or null
- "admission" (string): ticket price info as-is from the page, or null
- "reservation_required" (boolean): true if advance reservation/timed tickets are required or strongly recommended

すべて日本語で回答してください。

Return ONLY a JSON array. No markdown fences, no explanation."""

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

OUTPUT_DIR = Path("output")
CACHE_FILE = OUTPUT_DIR / "cache.json"
DETAIL_CACHE_FILE = OUTPUT_DIR / "detail_cache.json"


def load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_page(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding
    return resp.text


def clean_html(html: str) -> str | None:
    text = trafilatura.extract(html)

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    bs_text = soup.get_text(separator="\n")
    bs_text = re.sub(r"\n{3,}", "\n\n", bs_text).strip()

    if text and len(text.strip()) >= len(bs_text or ""):
        return text.strip()
    return bs_text if bs_text else None


def load_detail_cache() -> dict:
    if DETAIL_CACHE_FILE.exists():
        return json.loads(DETAIL_CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def save_detail_cache(cache: dict):
    DETAIL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DETAIL_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


DETAIL_TTL_DAYS = 7


def fetch_detail_text(url: str, selector: str, detail_cache: dict) -> str | None:
    """Fetch a detail page, extract text via CSS selector, with per-URL caching + TTL."""
    cached = detail_cache.get(url)
    if cached:
        fetched_at = cached.get("fetched_at", "")
        if fetched_at:
            from datetime import timedelta
            age = datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)
            if age < timedelta(days=DETAIL_TTL_DAYS):
                return cached.get("text")

    try:
        html = fetch_page(url)
    except Exception as e:
        print(f"    [WARN] detail fetch failed {url}: {e}")
        return None

    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one(selector)
    if not el:
        return None

    text = el.get_text(separator="\n").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)

    detail_cache[url] = {
        "text": text,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return text


def fmt_date(d: str) -> str | None:
    """Convert YYYYMMDD to YYYY-MM-DD."""
    if d and len(d) >= 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return None


def parse_mot(raw: str, today: str) -> list:
    """Parse MOT JSON API into event list (no summary/recommendation yet)."""
    data = json.loads(raw)
    today_compact = today.replace("-", "")
    events = []
    for item in data:
        end = item.get("end", "")
        if end < today_compact:
            continue
        crown = (item.get("crownName") or "").strip()
        title = (item.get("title") or "").strip()
        full_title = f"{crown} {title}".strip() if crown else title
        events.append({
            "title": full_title,
            "venue": "東京都現代美術館",
            "start_date": fmt_date(item.get("start", "")),
            "end_date": fmt_date(end),
            "start_time": None,
            "end_time": None,
            "closed_days": None,
            "admission": None,
            "reservation_required": False,
            "url": "https://www.mot-art-museum.jp" + item.get("permalink", ""),
            "summary": None,
            "recommendation": None,
            "detail_fetched": False,
        })
    return events


def parse_nact(raw: str, today: str) -> list:
    """Parse NACT JSON API into event list (no summary/recommendation yet)."""
    data = json.loads(raw)
    events = []
    for item in data:
        end = item.get("sp_ex_to", "")
        if not end or end < today:
            continue
        events.append({
            "title": item.get("sp_ex_title", ""),
            "venue": "国立新美術館",
            "start_date": item.get("sp_ex_from"),
            "end_date": end,
            "start_time": None,
            "end_time": None,
            "reservation_required": False,
            "url": "https://www.nact.jp" + item.get("sp_ex_permalink", ""),
            "summary": None,
            "recommendation": None,
        })
    return events


def parse_tokyonode(raw: str, today: str) -> list:
    """Parse TOKYO NODE JSON API into event list."""
    data = json.loads(raw).get("eventData", [])
    events = []
    for item in data:
        # Parse date: "2026.5.2" → "2026-05-02"
        end_raw = (item.get("date_end_short") or "").strip()
        start_raw = (item.get("date_start_short") or "").strip()
        def parse_dot_date(d):
            parts = d.split(".")
            if len(parts) == 3:
                return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
            return None
        start_date = parse_dot_date(start_raw)
        end_date = parse_dot_date(end_raw)

        # Skip past events
        if end_date and end_date < today:
            continue
        if not end_date and start_date and start_date < today:
            continue

        # Extract times from datetime_short: "2026.5.2 10:45:00"
        start_time = None
        end_time = None
        sdt = (item.get("date_start_datetime_short") or "").strip()
        edt = (item.get("date_end_datetime_short") or "").strip()
        if " " in sdt:
            start_time = sdt.split(" ")[1][:5]  # "10:45"
        if " " in edt:
            end_time = edt.split(" ")[1][:5]

        places = item.get("place", [])
        venue = "TOKYO NODE " + "・".join(places) if places else "TOKYO NODE"

        has_ticket = bool(item.get("ticket_url"))

        events.append({
            "title": (item.get("title") or "").strip(),
            "venue": venue,
            "start_date": start_date,
            "end_date": end_date,
            "start_time": start_time,
            "end_time": end_time,
            "reservation_required": has_ticket,
            "url": "https://www.tokyonode.jp" + item.get("this_url", ""),
            "summary": None,
            "recommendation": None,
        })
    return events


def parse_tobikan(raw: str, today: str) -> list:
    """Parse Tobikan CSV into event list."""
    import csv, io
    reader = csv.DictReader(io.StringIO(raw))
    events = []
    for row in reader:
        rid = row.get("id", "")
        # Skip AC (accessibility programs) and empty rows
        if not rid or rid.startswith("a"):
            continue
        period = row.get("period", "").strip()
        if not period or "-" not in period:
            continue

        # Parse period: "2026/1/27-4/12" or "2026/4/28-7/5"
        parts = period.split("-")
        start_str = parts[0].strip()  # "2026/1/27"
        end_str = parts[1].strip()    # "4/12"

        s_parts = start_str.split("/")
        if len(s_parts) != 3:
            continue
        year = s_parts[0]
        start_date = f"{year}-{int(s_parts[1]):02d}-{int(s_parts[2]):02d}"

        e_parts = end_str.split("/")
        if len(e_parts) == 2:
            end_month, end_day = int(e_parts[0]), int(e_parts[1])
            # If end month < start month, it's next year
            start_month = int(s_parts[1])
            end_year = int(year) + 1 if end_month < start_month else int(year)
            end_date = f"{end_year}-{end_month:02d}-{end_day:02d}"
        else:
            end_date = None

        # Skip past events
        if end_date and end_date < today:
            continue

        title = (row.get("title") or "").replace("<br>", " ").strip()
        link = row.get("link", "")
        url = f"https://www.tobikan.jp{link}" if link else None
        fee = row.get("fee", "")

        events.append({
            "title": title,
            "venue": "東京都美術館",
            "start_date": start_date,
            "end_date": end_date,
            "start_time": None,
            "end_time": None,
            "reservation_required": fee == "1",  # fee=1 means paid/ticketed
            "url": url,
            "summary": None,
            "recommendation": None,
        })
    return events


PARSERS = {
    "mot": parse_mot,
    "tobikan": parse_tobikan,
    "nact": parse_nact,
    "tokyonode": parse_tokyonode,
}


def _call_enrich_llm(client: anthropic.Anthropic, system_prompt: str, user_msg: str) -> list:
    """Call LLM for enrichment, return parsed list."""
    resp = client.messages.create(
        model="MiniMax-M2.7",
        max_tokens=8192,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
        temperature=0.1,
    )
    raw = next(b.text for b in resp.content if b.type == "text").strip()

    cleaned = re.sub(r"^```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        print(f"  [WARN] Enrich LLM returned unparseable JSON")
        return []


def enrich_events(client: anthropic.Anthropic, events: list, detail_texts: dict | None = None) -> list:
    """Enrich events with LLM. If detail_texts provided, use detail prompt."""
    if not events:
        return events

    if detail_texts:
        # Detail mode: send title + venue + detail page text
        items = []
        for i, e in enumerate(events):
            item = {"index": i, "title": e["title"], "venue": e["venue"]}
            dt = detail_texts.get(e.get("url"))
            if dt:
                # Send full detail text — basic info (hours/admission) is often at the end
                item["detail"] = dt
            items.append(item)
        enrichments = _call_enrich_llm(client, ENRICH_PROMPT_DETAIL, json.dumps(items, ensure_ascii=False))
    else:
        # Basic mode: title + venue only
        items = [{"index": i, "title": e["title"], "venue": e["venue"]} for i, e in enumerate(events)]
        enrichments = _call_enrich_llm(client, ENRICH_PROMPT_BASIC, json.dumps(items, ensure_ascii=False))

    # Apply enrichments
    enrich_map = {e["index"]: e for e in enrichments if isinstance(e, dict)}
    for i, event in enumerate(events):
        em = enrich_map.get(i, {})
        event["summary"] = em.get("summary", "")
        event["recommendation"] = em.get("recommendation", "normal")
        if detail_texts:
            has_detail = bool(detail_texts.get(event.get("url")))
            event["detail_fetched"] = has_detail
            if has_detail:
                # Override fields from LLM detail extraction
                if em.get("venue"):
                    event["venue"] = em["venue"]
                if em.get("start_time"):
                    event["start_time"] = em["start_time"]
                if em.get("end_time"):
                    event["end_time"] = em["end_time"]
                if em.get("closed_days"):
                    event["closed_days"] = em["closed_days"]
                if em.get("admission"):
                    event["admission"] = em["admission"]
                if "reservation_required" in em:
                    event["reservation_required"] = em["reservation_required"]

    return events


def extract_events(client: anthropic.Anthropic, source_name: str, source_url: str, text: str) -> list:
    user_msg = f"Source: {source_name}\nURL: {source_url}\n\nPage content:\n{text}"
    resp = client.messages.create(
        model="MiniMax-M2.7",
        max_tokens=8192,
        system=SYSTEM_PROMPT_TEMPLATE.format(today=datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        messages=[
            {"role": "user", "content": user_msg},
        ],
        temperature=0.1,
    )
    raw = next(b.text for b in resp.content if b.type == "text").strip()

    # Strip markdown fences if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    # Extract the JSON array from the response (find first [ to last ])
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        print(f"[WARN] Unparseable JSON from LLM for {source_name}:")
        print(raw[:500])
        return []


def save_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  saved: {path}")


def main():
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

    result = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sources": [],
    }

    for src in SOURCES:
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
            print(f"FETCH ERROR: {e}", end="")
            cached = cache.get(src["name"])
            if cached and cached.get("events"):
                print(f" → using cached {len(cached['events'])} events")
                result["sources"].append({
                    "name": src["name"],
                    "url": src["url"],
                    "content_length": 0,
                    "events": cached["events"],
                })
            else:
                print(" → no cache available, skipping")
            continue

        if not text:
            print("no content extracted, skipping")
            continue

        save_file(run_dir / f"{slug}.txt", text)
        print(f"  {len(text)} chars")

        # Check cache
        h = content_hash(text)
        cached = cache.get(src["name"])
        if cached and cached["content_hash"] == h:
            events = cached["events"]
            # For parser sources with detail_selector, check if any details expired
            selector = src.get("detail_selector")
            if selector and events:
                from datetime import timedelta
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
                    print(f"  ⟳ {len(expired)} detail(s) expired, refreshing...", end="", flush=True)
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
                    events = enrich_events(client, events, detail_texts)
                    print(f" done")
                    cache[src["name"]] = {
                        "content_hash": h,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "events": events,
                    }
                else:
                    print(f"  ✓ cache hit ({cached['fetched_at']})")
            else:
                print(f"  ✓ cache hit ({cached['fetched_at']})")
        elif "parser" in src:
            # Structured source: parse directly, then enrich with lightweight LLM
            parser_fn = PARSERS[src["parser"]]
            events = parser_fn(text, today)
            print(f"  ⚙ parsed {len(events)} events", end="", flush=True)

            # Fetch detail pages if selector configured
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
            events = enrich_events(client, events, detail_texts)
            print(f" done")
            cache[src["name"]] = {
                "content_hash": h,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "events": events,
            }
        else:
            events = extract_events(client, src["name"], src["url"], text)
            print(f"  → {len(events)} events extracted (LLM called)")
            cache[src["name"]] = {
                "content_hash": h,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "events": events,
            }

        result["sources"].append({
            "name": src["name"],
            "url": src["url"],
            "content_length": len(text),
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
