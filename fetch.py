"""HTTP fetching, HTML cleaning, and cache management."""
import hashlib
import json
import re
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


# --- Cache ---

def load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def load_detail_cache() -> dict:
    if DETAIL_CACHE_FILE.exists():
        return json.loads(DETAIL_CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def save_detail_cache(cache: dict):
    DETAIL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DETAIL_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- HTTP ---

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


def extract_list_text(raw_html: str, src: dict) -> str | None:
    """Extract text from HTML using list_selector + link_selector."""
    list_sel = src.get("list_selector")
    if not list_sel:
        return clean_html(raw_html)

    soup = BeautifulSoup(raw_html, "html.parser")
    items = soup.select(list_sel)
    link_sel = src.get("link_selector")
    base_url = "/".join(src["url"].split("/", 3)[:3])
    parts = []
    PLACEHOLDER_IMGS = {"_blank.png", "noimage", "no_image", "no-image", "spacer"}
    for el in items:
        el_text = re.sub(r"\n{3,}", "\n\n", el.get_text(separator="\n").strip())
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
        parts.append(el_text)
    return "\n\n".join(parts) if parts else None


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
