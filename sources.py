"""Source configurations, parsers, and prompt templates."""
import json

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
        "detail_selector": "#ExhibitMain",
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

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
- summary (string): 3-4 sentence description of the event content in Japanese
- recommendation (string): based on the exhibition title, artist/theme reputation, and venue prestige, classify as one of:
  "must_see" — internationally significant exhibitions, major retrospectives of renowned artists, or blockbuster museum collaborations
  "recommended" — notable solo exhibitions, well-known artists, or topical/unique themes
  "normal" — group shows, public competitions (公募展), calligraphy/craft guild exhibitions, or insufficient information to judge

すべて日本語で回答してください。

Return ONLY a JSON array. No markdown fences, no explanation.
If zero events found, return []."""

ENRICH_PROMPT_BASIC = """Given a list of art exhibition titles and venues, add a Japanese summary (3-4 sentences) and a recommendation classification for each.

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
- "summary" (string): 3-4 sentence Japanese description based on the detail content
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


# --- Parsers ---

def _fmt_date(d: str) -> str | None:
    """Convert YYYYMMDD to YYYY-MM-DD."""
    if d and len(d) >= 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return None


def parse_mot(raw: str, today: str) -> list:
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
            "start_date": _fmt_date(item.get("start", "")),
            "end_date": _fmt_date(end),
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
    data = json.loads(raw).get("eventData", [])
    events = []
    for item in data:
        end_raw = (item.get("date_end_short") or "").strip()
        start_raw = (item.get("date_start_short") or "").strip()

        def parse_dot_date(d):
            parts = d.split(".")
            if len(parts) == 3:
                return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
            return None

        start_date = parse_dot_date(start_raw)
        end_date = parse_dot_date(end_raw)

        if end_date and end_date < today:
            continue
        if not end_date and start_date and start_date < today:
            continue

        start_time = None
        end_time = None
        sdt = (item.get("date_start_datetime_short") or "").strip()
        edt = (item.get("date_end_datetime_short") or "").strip()
        if " " in sdt:
            start_time = sdt.split(" ")[1][:5]
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
    import csv
    import io

    reader = csv.DictReader(io.StringIO(raw))
    events = []
    for row in reader:
        rid = row.get("id", "")
        if not rid or rid.startswith("a"):
            continue
        period = row.get("period", "").strip()
        if not period or "-" not in period:
            continue

        parts = period.split("-")
        start_str = parts[0].strip()
        end_str = parts[1].strip()

        s_parts = start_str.split("/")
        if len(s_parts) != 3:
            continue
        year = s_parts[0]
        start_date = f"{year}-{int(s_parts[1]):02d}-{int(s_parts[2]):02d}"

        e_parts = end_str.split("/")
        if len(e_parts) == 2:
            end_month, end_day = int(e_parts[0]), int(e_parts[1])
            start_month = int(s_parts[1])
            end_year = int(year) + 1 if end_month < start_month else int(year)
            end_date = f"{end_year}-{end_month:02d}-{end_day:02d}"
        else:
            end_date = None

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
            "reservation_required": fee == "1",
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
