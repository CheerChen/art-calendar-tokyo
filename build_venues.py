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
# Match raw event venue strings to canonical venue names in venues.json.
#
# Strategy (replaces fragile regex suffix-stripping):
#   1. Alias lookup — venues.json entries may carry an "aliases" array of
#      known alternate names; an exact match wins immediately.
#   2. Longest-substring-contain — if any canonical key is a substring of the
#      raw venue, the longest such key wins (greedy). This handles "館名 + 部屋"
#      and "館名（新館2階）" without per-site rules.
#   3. Fall through to the raw string — it becomes a new canonical to geocode.
#
# build_venues.py loads the *existing* venues.json (if any) to drive matching
# on re-runs; the very first seed run has no venues.json so everything falls
# through and raw strings become canonicals as-is.


def build_matchers(existing_venues: dict) -> tuple[list[str], dict[str, str]]:
    """From existing venues.json, build (canonicals longest-first, alias->canonical map)."""
    canonicals = sorted(existing_venues.keys(), key=len, reverse=True)
    alias_map: dict[str, str] = {}
    for key, entry in existing_venues.items():
        for alias in (entry.get("aliases") or []):
            alias_map[alias] = key
    return canonicals, alias_map


def normalize(raw: str, canonicals: list[str], alias_map: dict[str, str]) -> str:
    """Map a raw event venue string to its canonical venue name."""
    s = raw.strip()
    if s in alias_map:
        return alias_map[s]
    for c in canonicals:  # longest-first
        if c in s:
            return c
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

    # Load existing venues.json for contain+alias matching.
    existing_venues: dict = {}
    if VENUES_JSON.exists():
        existing_venues = json.loads(VENUES_JSON.read_text())
    canon_keys, alias_map = build_matchers(existing_venues)

    # raw -> canonical map + counts
    raw_to_canonical: dict[str, str] = {}
    canonical_counts: Counter[str] = Counter()
    for v in raw_venues:
        c = normalize(v, canon_keys, alias_map)
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
