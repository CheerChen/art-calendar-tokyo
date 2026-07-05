"""One-off: build venues.json draft by geocoding canonical venue names.

Pipeline:
  result.json -> normalize venue strings to canonical -> geocode canonicals
  via transit.ls8h.com places/suggest -> write venues.json + review report.

Idempotent: caches geocode results to output/geocode_cache.json so re-runs
don't re-hit the API. Run once to produce the seed venues.json; afterwards
this file is hand-maintained and only re-run when new venues appear.

Usage:
  uv run python build_venues.py            # geocode + write venues.json
  uv run python build_venues.py --dry-run  # only normalize + print canonicals
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
RESULT_JSON = ROOT / "result.json"
VENUES_JSON = ROOT / "venues.json"
GEOCODE_CACHE = ROOT / "output" / "geocode_cache.json"
SUGGEST_URL = "https://api.transit.ls8h.com/api/v1/places/suggest"

# --- normalization -----------------------------------------------------------
# Strip room/exhibition-room suffixes so multiple events at the same physical
# venue collapse to one canonical name. Conservative: only strip patterns that
# are unambiguously room-level. When unsure, keep the full string (fail loud).

# Patterns applied in order; each strips a trailing room descriptor.
ROOM_SUFFIXES = [
    r"\s+企画展示室.*$",
    r"\s+コレクション展示室.*$",
    r"\s+本館.*$",
    r"\s+新館.*$",
    r"\s+ギャラリー\s*\d.*$",
    r"\s+ギャラリー\s*[1-9].*$",
    r"\s+4Fコリドール$",
    r"\s+本部棟.*$",
    r"\s+じゆうエリア$",
    r"\s+建築倉庫$",
    r"\s+第\d.*展示室.*$",
    r"\s+\d+階.*$",
]

# Whole-string replacements for venues where a cleaner canonical name helps
# the geocoder, but the room suffix rule above doesn't catch it.
CANONICAL_OVERRIDES = {
    # TOKYO NODE rooms -> the building itself (geocoder won't find room names)
    "TOKYO NODE GALLERY A/B/C": "TOKYO NODE",
    "TOKYO NODE GALLERY A/B/C・TOKYO NODE LAB": "TOKYO NODE",
    "TOKYO NODE HALL": "TOKYO NODE",
}


def normalize(raw: str) -> str:
    """Map a raw event venue string to its canonical venue name."""
    s = raw.strip()
    if s in CANONICAL_OVERRIDES:
        return CANONICAL_OVERRIDES[s]
    for pat in ROOM_SUFFIXES:
        new = re.sub(pat, "", s)
        if new != s:
            return new.strip()
    return s


# --- geocoding ---------------------------------------------------------------
def load_cache() -> dict:
    if GEOCODE_CACHE.exists():
        return json.loads(GEOCODE_CACHE.read_text())
    return {}


def save_cache(cache: dict) -> None:
    GEOCODE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    GEOCODE_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def suggest(query: str, limit: int = 5) -> list:
    """Call places/suggest and return the raw places list."""
    qs = urllib.parse.urlencode({"q": query, "limit": limit})
    url = f"{SUGGEST_URL}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "art-calendar-tokyo/seed"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("places", [])


def pick_best(query: str, places: list) -> dict | None:
    """Pick the most likely match for a venue name from suggest results.

    Heuristics, in priority order:
      1. kind == "place" with description mentioning museum/施設/ギャラリー/art
      2. highest weight among kind == "place"
      3. highest weight overall
    Returns {"place": ..., "confidence": "high"|"mid"|"low"} or None.
    """
    if not places:
        return None
    place_kind = [p for p in places if p.get("kind") == "place"]
    desc_kw = re.compile(r"museum|施設|ギャラリー|art|gallery|design", re.I)

    def desc(p):
        return (p.get("description") or "") + " " + (p.get("nameEn") or "")

    museum_like = [p for p in place_kind if desc_kw.search(desc(p))]
    pool = museum_like or place_kind or places
    pool.sort(key=lambda p: p.get("weight", 0), reverse=True)
    best = pool[0]
    if museum_like:
        conf = "high"
    elif place_kind:
        conf = "mid"
    else:
        conf = "low"
    return {"place": best, "confidence": conf}


# --- main --------------------------------------------------------------------
def main(dry_run: bool) -> None:
    data = json.loads(RESULT_JSON.read_text())
    raw_venues: list[str] = []
    for src in data["sources"]:
        for ev in src["events"]:
            v = (ev.get("venue") or "").strip()
            if v:
                raw_venues.append(v)

    # raw -> canonical map + counts
    raw_to_canonical: dict[str, str] = {}
    canonical_counts: Counter[str] = Counter()
    for v in raw_venues:
        c = normalize(v)
        raw_to_canonical[v] = c
        canonical_counts[c] += 1

    canonicals = sorted(canonical_counts.keys())
    print(f"raw venue strings: {len(set(raw_venues))}")
    print(f"canonical venues:  {len(canonicals)}")
    print()
    print("=== canonical -> (raw variants | event count) ===")
    by_canonical: dict[str, list[str]] = {}
    for raw, can in raw_to_canonical.items():
        by_canonical.setdefault(can, []).append(raw)
    for can in canonicals:
        variants = by_canonical[can]
        print(f"  [{canonical_counts[can]:2}] {can}")
        for v in variants:
            if v != can:
                print(f"        <- {v}")

    if dry_run:
        print("\n(--dry-run: skipping geocode)")
        return

    cache = load_cache()
    venues: dict[str, dict] = {}
    report: list[dict] = []

    for can in canonicals:
        if can in cache and cache[can] is not None:
            entry = cache[can]
        else:
            places = suggest(can)
            picked = pick_best(can, places)
            if picked is None:
                entry = None
            else:
                p = picked["place"]
                entry = {
                    "lat": p.get("lat"),
                    "lng": p.get("lon"),
                    "address": None,  # places/suggest doesn't return address; left for manual fill
                    "endpoint": p.get("endpoint"),
                    "source": p.get("source"),
                    "matched_name": p.get("name"),
                    "name_en": p.get("nameEn"),
                    "description": p.get("description"),
                    "kind": p.get("kind"),
                    "confidence": picked["confidence"],
                    "verified": picked["confidence"] == "high",
                }
            cache[can] = entry
            save_cache(cache)
            time.sleep(0.3)  # be polite; API is keyless

        if entry is None:
            report.append({"canonical": can, "status": "not_found", "count": canonical_counts[can]})
            venues[can] = {"lat": None, "lng": None, "verified": False, "note": "geocode failed; needs manual entry"}
        else:
            venues[can] = entry
            report.append({
                "canonical": can,
                "status": entry["confidence"],
                "count": canonical_counts[can],
                "matched": entry["matched_name"],
                "lat": entry["lat"],
                "lng": entry["lng"],
            })

    VENUES_JSON.write_text(json.dumps(venues, ensure_ascii=False, indent=2))
    print(f"\nwrote {VENUES_JSON}")
    print()
    print("=== review report ===")
    for r in sorted(report, key=lambda x: (x["status"] != "not_found", x["canonical"])):
        flag = {"high": "OK ", "mid": "MID", "low": "LOW", "not_found": "MISS"}[r["status"]]
        latlng = f"{r.get('lat')},{r.get('lng')}" if r.get("lat") is not None else "-"
        print(f"  [{flag}] ({r['count']:2}) {r['canonical']}")
        if r["status"] != "not_found":
            print(f"            -> {r['matched']}  @  {latlng}")
    misses = [r for r in report if r["status"] == "not_found"]
    mids = [r for r in report if r["status"] == "mid"]
    lows = [r for r in report if r["status"] == "low"]
    print()
    print(f"summary: {len(report)} canonicals | high={len(report)-len(misses)-len(mids)-len(lows)} mid={len(mids)} low={len(lows)} miss={len(misses)}")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
