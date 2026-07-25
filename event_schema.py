"""Canonical event schema and output validation."""
from datetime import date, datetime


EVENT_SCHEMA_VERSION = 2
EVENT_FIELDS = (
    "title",
    "venue",
    "venue_key",
    "start_date",
    "end_date",
    "start_time",
    "end_time",
    "closed_days",
    "admission",
    "reservation_required",
    "url",
    "image",
    "summary",
    "recommendation",
    "detail_fetched",
)
RECOMMENDATIONS = {"must_see", "recommended", "normal"}


def make_event(title: str, venue: str, **values) -> dict:
    """Create an event with every schema field present."""
    event = {field: None for field in EVENT_FIELDS}
    event.update({
        "title": title,
        "venue": venue,
        "reservation_required": False,
        "recommendation": None,
        "detail_fetched": False,
    })
    unknown = set(values) - set(EVENT_FIELDS)
    if unknown:
        raise TypeError(f"unknown event field(s): {', '.join(sorted(unknown))}")
    event.update(values)
    return event


def _string_or_none(value, field: str, warnings: list[str]):
    if value is None:
        return None
    if not isinstance(value, str):
        warnings.append(f"{field} must be a string or null")
        return None
    value = value.strip()
    return value or None


def _date_or_none(value, field: str, warnings: list[str]):
    value = _string_or_none(value, field, warnings)
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        warnings.append(f"{field} has invalid ISO date: {value!r}")
        return None
    if parsed.isoformat() != value:
        warnings.append(f"{field} has non-canonical ISO date: {value!r}")
        return None
    return value


def _time_or_none(value, field: str, warnings: list[str]):
    value = _string_or_none(value, field, warnings)
    if value is None:
        return None
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError:
        warnings.append(f"{field} has invalid 24-hour time: {value!r}")
        return None
    canonical = parsed.strftime("%H:%M")
    if canonical != value:
        warnings.append(f"{field} has non-canonical time: {value!r}")
        return None
    return value


def normalize_event(raw, *, source_url: str | None = None) -> tuple[dict | None, list[str]]:
    """Normalize one untrusted parser/LLM event into the canonical schema."""
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return None, [f"event must be an object, got {type(raw).__name__}"]

    title = _string_or_none(raw.get("title"), "title", warnings)
    if not title:
        return None, [*warnings, "event dropped: title is empty"]

    venue = _string_or_none(raw.get("venue"), "venue", warnings)
    if not venue:
        warnings.append("venue is empty")

    event = make_event(
        title=title,
        venue=venue,
        start_date=_date_or_none(raw.get("start_date"), "start_date", warnings),
        end_date=_date_or_none(raw.get("end_date"), "end_date", warnings),
        start_time=_time_or_none(raw.get("start_time"), "start_time", warnings),
        end_time=_time_or_none(raw.get("end_time"), "end_time", warnings),
        closed_days=_string_or_none(raw.get("closed_days"), "closed_days", warnings),
        admission=_string_or_none(raw.get("admission"), "admission", warnings),
        url=_string_or_none(raw.get("url"), "url", warnings) or source_url,
        image=_string_or_none(raw.get("image"), "image", warnings),
        summary=_string_or_none(raw.get("summary"), "summary", warnings),
        venue_key=_string_or_none(raw.get("venue_key"), "venue_key", warnings),
    )

    for field in ("reservation_required", "detail_fetched"):
        value = raw.get(field, False)
        if not isinstance(value, bool):
            warnings.append(f"{field} must be a boolean")
            value = False
        event[field] = value

    recommendation = raw.get("recommendation")
    if not isinstance(recommendation, str) or recommendation not in RECOMMENDATIONS:
        if recommendation not in (None, ""):
            warnings.append(f"recommendation has invalid value: {recommendation!r}")
        recommendation = "normal"
    event["recommendation"] = recommendation

    if event["start_date"] and event["end_date"] and event["start_date"] > event["end_date"]:
        warnings.append(
            f"end_date precedes start_date: {event['start_date']} > {event['end_date']}"
        )
        event["end_date"] = None

    return event, warnings


def normalize_events(events, *, source_url: str | None = None) -> tuple[list[dict], list[str]]:
    """Normalize a collection and prefix warnings with stable event indexes."""
    if not isinstance(events, list):
        return [], [f"events must be an array, got {type(events).__name__}"]

    normalized = []
    warnings = []
    for index, raw in enumerate(events):
        event, event_warnings = normalize_event(raw, source_url=source_url)
        warnings.extend(f"event[{index}]: {warning}" for warning in event_warnings)
        if event is not None:
            normalized.append(event)
    return normalized, warnings
