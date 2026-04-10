"""One-time script to backfill images for existing result.json.

- For API sources (MOT/NACT/TOKYONODE): re-fetch API JSON and map images by URL/title.
- For other sources with event URLs: try to extract og:image from the event page.
"""
import json

import requests
from bs4 import BeautifulSoup

from sources import HEADERS

API_SOURCES = {
    "東京都現代美術館": {
        "api": "https://www.mot-art-museum.jp/json/exhibitions/exhibitions.json",
        "base": "https://www.mot-art-museum.jp",
        "url_field": "permalink",
        "image_field": "imagePc",
    },
    "国立新美術館": {
        "api": "https://www.nact.jp/exhibition_special/exhibition_special_ca.json",
        "base": "https://www.nact.jp",
        "url_field": "sp_ex_permalink",
        "image_field": "sp_ex_thumbnail",
    },
    "TOKYO NODE": {
        "api": "https://www.tokyonode.jp/assets/json/tn_all_events.json",
        "base": "https://www.tokyonode.jp",
        "url_field": "this_url",
        "image_field": "img_key",
        "wrap": "eventData",
    },
}


def extract_og_image(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"    [ERR] {e}")
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    for prop in ["og:image", "twitter:image"]:
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            content = tag["content"]
            if content.startswith("//"):
                content = "https:" + content
            return content
    return None


def backfill_api(data: dict) -> int:
    """Backfill images from API JSON for the 3 API sources."""
    count = 0
    for src in data["sources"]:
        cfg = API_SOURCES.get(src["name"])
        if not cfg:
            continue

        print(f"\n[API] {src['name']}")
        try:
            resp = requests.get(cfg["api"], headers=HEADERS, timeout=30)
            resp.raise_for_status()
            api_data = resp.json()
        except Exception as e:
            print(f"  fetch failed: {e}")
            continue

        if "wrap" in cfg:
            api_data = api_data.get(cfg["wrap"], [])

        # Build URL -> image map
        img_map = {}
        for item in api_data:
            url_val = item.get(cfg["url_field"], "")
            img_val = item.get(cfg["image_field"], "")
            if url_val and img_val:
                full_url = cfg["base"] + url_val
                full_img = cfg["base"] + img_val
                img_map[full_url] = full_img

        for ev in src["events"]:
            if ev.get("image"):
                continue
            img = img_map.get(ev.get("url", ""))
            if img:
                ev["image"] = img
                count += 1
                print(f"  [OK] {ev['title'][:40]}")

    return count


def backfill_og(data: dict) -> int:
    """Backfill og:image for non-API sources."""
    count = 0
    for src in data["sources"]:
        if src["name"] in API_SOURCES:
            continue
        has_urls = [ev for ev in src["events"] if ev.get("url") and not ev.get("image")]
        if not has_urls:
            continue

        print(f"\n[OG] {src['name']} ({len(has_urls)} events)")
        for ev in has_urls:
            img = extract_og_image(ev["url"])
            if img:
                ev["image"] = img
                count += 1
                print(f"  [OK] {ev['title'][:40]}")
            else:
                print(f"  [--] {ev['title'][:40]}")

    return count


def main():
    with open("result.json", encoding="utf-8") as f:
        data = json.load(f)

    n1 = backfill_api(data)
    n2 = backfill_og(data)

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nDone: {n1} from API, {n2} from og:image, total {n1 + n2}")


if __name__ == "__main__":
    main()
