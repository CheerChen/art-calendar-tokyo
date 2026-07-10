"""HTTP fetching, HTML cleaning, and cache management."""
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import trafilatura
from bs4 import BeautifulSoup

from sources import HEADERS

OUTPUT_DIR = Path("output")
CACHE_FILE = OUTPUT_DIR / "cache.json"
DETAIL_CACHE_FILE = OUTPUT_DIR / "detail_cache.json"
DETAIL_TTL_DAYS = 7

# Visible-text threshold for SPA-shell heuristic (aligned with ax spaNote).
SPA_MIN_BODY_CHARS = 200


# --- Cache ---

def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            print("  [WARN] cache.json empty or corrupt, starting fresh")
    return {}


def save_cache(cache: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def load_detail_cache() -> dict:
    if DETAIL_CACHE_FILE.exists():
        try:
            return json.loads(DETAIL_CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            print("  [WARN] detail_cache.json empty or corrupt, starting fresh")
    return {}


def save_detail_cache(cache: dict):
    DETAIL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DETAIL_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- HTTP ---

def fetch_page(url: str, retries: int = 3) -> str:
    import time
    for attempt in range(retries):
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 403 and attempt < retries - 1:
            wait = (attempt + 1) * 5
            print(f" [403 retry {attempt + 1}/{retries}, wait {wait}s]", end="", flush=True)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        # Only override when the server omitted a charset (requests defaults
        # to ISO-8859-1 in that case). Trust an explicit charset declaration —
        # apparent_encoding is a statistical guess that can misfire on CI hosts
        # and corrupt UTF-8 pages (e.g. warehouseofart.org).
        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
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


# --- Extract diagnostics (P0 never-silent / P1 SPA shell) ---

@dataclass
class ListExtractResult:
    """Structured list-page extract with completeness stats (never silent)."""
    text: str | None
    item_count: int  # list_selector match count; -1 when no list_selector
    has_list_selector: bool
    empty_url: int = 0
    empty_image: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_meta(self) -> dict:
        return {
            "item_count": self.item_count,
            "empty_url": self.empty_url,
            "empty_image": self.empty_image,
            "warnings": list(self.warnings),
        }


def detect_spa_shell(html: str, *, min_body_chars: int = SPA_MIN_BODY_CHARS) -> str | None:
    """Heuristic: tiny visible body + scripts → likely JS-rendered SPA."""
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body
    text = re.sub(r"\s+", " ", (body.get_text() if body else "")).strip()
    n_scripts = len(soup.find_all("script"))
    if len(text) < min_body_chars and n_scripts > 0:
        return (
            f"body has {len(text)} chars visible text and {n_scripts} script(s) "
            "— likely JS-rendered SPA; raw HTML extract is useless"
        )
    return None


def report_event_stats(name: str, events: list, *, kind: str = "parser") -> dict:
    """Print never-silent stats for parser/LLM event lists. Returns extract_meta."""
    n = len(events)
    empty_url = sum(1 for e in events if isinstance(e, dict) and not e.get("url"))
    empty_image = sum(1 for e in events if isinstance(e, dict) and not e.get("image"))
    warnings: list[str] = []
    print(
        f"  [EXTRACT] {name} ({kind}): {n} events — "
        f"url empty: {empty_url}, image empty: {empty_image}",
        flush=True,
    )
    if n == 0:
        w = f"{kind} returned 0 events"
        print(f"  [WARN] {name}: {w}", flush=True)
        warnings.append(w)
    elif empty_url == n:
        w = "all events missing url"
        print(f"  [WARN] {name}: {w}", flush=True)
        warnings.append(w)
    elif empty_url > n * 0.5:
        w = f"url empty: {empty_url}/{n}"
        print(f"  [WARN] {name}: {w}", flush=True)
        warnings.append(w)
    return {
        "item_count": n,
        "empty_url": empty_url,
        "empty_image": empty_image,
        "warnings": warnings,
    }


def extract_list(raw_html: str, src: dict) -> ListExtractResult:
    """Extract list-page text with completeness report (aligned with ax rowStats)."""
    name = src.get("name", "?")
    list_sel = src.get("list_selector")
    warnings: list[str] = []

    if not list_sel:
        text = clean_html(raw_html)
        if not text or len(text) < SPA_MIN_BODY_CHARS:
            spa = detect_spa_shell(raw_html)
            if spa:
                print(f"  [SPA?] {name}: {spa}", flush=True)
                warnings.append(f"SPA? {spa}")
            elif not text:
                print(f"  [EXTRACT] {name}: no list_selector, clean_html returned empty", flush=True)
            else:
                print(
                    f"  [EXTRACT] {name}: no list_selector, clean_html {len(text)} chars (short)",
                    flush=True,
                )
        else:
            print(f"  [EXTRACT] {name}: no list_selector, clean_html {len(text)} chars", flush=True)
        return ListExtractResult(
            text=text,
            item_count=-1,
            has_list_selector=False,
            warnings=warnings,
        )

    soup = BeautifulSoup(raw_html, "html.parser")
    items = soup.select(list_sel)
    link_sel = src.get("link_selector")
    base_url = "/".join(src["url"].split("/", 3)[:3])
    parts = []
    empty_url = 0
    empty_image = 0
    PLACEHOLDER_IMGS = {"_blank.png", "noimage", "no_image", "no-image", "spacer"}

    for el in items:
        el_text = re.sub(r"\n{3,}", "\n\n", el.get_text(separator="\n").strip())
        has_url = False
        has_image = False

        # Extract first meaningful image
        img = el.find("img")
        if img:
            img_url = img.get("data-pcimg") or img.get("src") or ""
            if img_url and not any(p in img_url.lower() for p in PLACEHOLDER_IMGS):
                if img_url.startswith("/"):
                    img_url = base_url + img_url
                elif not img_url.startswith("http"):
                    img_url = src["url"].rsplit("/", 1)[0] + "/" + img_url
                el_text = f"[IMAGE: {img_url}]\n{el_text}"
                has_image = True
        if not has_image:
            empty_image += 1

        # Extract links
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
            has_url = True
        if not has_url:
            empty_url += 1

        parts.append(el_text)

    item_count = len(items)
    text = "\n\n".join(parts) if parts else None

    if item_count == 0:
        spa = detect_spa_shell(raw_html)
        if spa:
            print(f"  [SPA?] {name}: list_selector matched 0 items; {spa}", flush=True)
            warnings.append(f"SPA? list_selector matched 0 — {spa}")
        else:
            print(
                f"  [DRIFT?] {name}: list_selector matched 0 items — check: {list_sel}",
                flush=True,
            )
            warnings.append(f"DRIFT? list_selector matched 0 — check: {list_sel}")
    else:
        print(
            f"  [EXTRACT] {name}: {item_count} items — "
            f"url empty: {empty_url}, image empty: {empty_image}",
            flush=True,
        )
        if empty_url == item_count:
            w = "all items missing URL — check link_selector"
            print(f"  [WARN] {name}: {w}", flush=True)
            warnings.append(w)
        elif empty_url > item_count * 0.5:
            w = f"url empty: {empty_url}/{item_count}"
            print(f"  [WARN] {name}: {w}", flush=True)
            warnings.append(w)

    return ListExtractResult(
        text=text,
        item_count=item_count,
        has_list_selector=True,
        empty_url=empty_url,
        empty_image=empty_image,
        warnings=warnings,
    )


def extract_list_text(raw_html: str, src: dict) -> str | None:
    """Backward-compatible wrapper: text only (still prints extract diagnostics)."""
    return extract_list(raw_html, src).text


def extract_og_image(html: str) -> str | None:
    """Extract og:image or twitter:image from HTML."""
    soup = BeautifulSoup(html, "html.parser")
    for prop in ["og:image", "twitter:image"]:
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            return tag["content"]
    return None


def fetch_detail_text(url: str, selector: str, detail_cache: dict) -> str | None:
    """Fetch a detail page, extract text via CSS selector, with per-URL caching + TTL."""
    cached = detail_cache.get(url)
    if cached:
        fetched_at = cached.get("fetched_at", "")
        if fetched_at:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)
            if age < timedelta(days=DETAIL_TTL_DAYS):
                return cached.get("text")

    try:
        html = fetch_page(url)
    except Exception as e:
        print(f"    [WARN] detail fetch failed {url}: {e}")
        return None

    soup = BeautifulSoup(html, "html.parser")
    els = soup.select(selector)
    if not els:
        return None

    text = "\n\n".join(el.get_text(separator="\n").strip() for el in els)
    text = re.sub(r"\n{3,}", "\n\n", text)

    image = extract_og_image(html)

    detail_cache[url] = {
        "text": text,
        "image": image,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return text


# --- File output ---

def save_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  saved: {path}")
