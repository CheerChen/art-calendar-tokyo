"""Shared venue-name matching for scraper output and venue maintenance."""
import json
from pathlib import Path


VENUES_FILE = Path(__file__).parent / "venues.json"


def load_venues(path: Path = VENUES_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_matchers(venues: dict) -> tuple[list[str], dict[str, str]]:
    """Build canonical names longest-first and an alias-to-canonical map."""
    canonicals = sorted(venues.keys(), key=len, reverse=True)
    aliases = {
        alias: canonical
        for canonical, entry in venues.items()
        for alias in (entry.get("aliases") or [])
    }
    return canonicals, aliases


def match_venue(
    raw: str | None,
    canonicals: list[str],
    alias_map: dict[str, str],
) -> str | None:
    """Return a known canonical venue key, or None when no key matches."""
    value = (raw or "").strip()
    if not value:
        return None
    if value in alias_map:
        return alias_map[value]
    for canonical in canonicals:
        if canonical in value:
            return canonical
    return None


def normalize_venue(
    raw: str | None,
    canonicals: list[str],
    alias_map: dict[str, str],
) -> str:
    """Return a known key when possible, otherwise preserve the raw name."""
    value = (raw or "").strip()
    return match_venue(value, canonicals, alias_map) or value
