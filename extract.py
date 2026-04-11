"""LLM-based event extraction and enrichment."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from sources import ENRICH_PROMPT_BASIC, ENRICH_PROMPT_DETAIL, SYSTEM_PROMPT_TEMPLATE

MIN_CONTENT_FOR_RETRY = 500
MAX_UNICODE_RETRIES = 1

# Korean, Cyrillic, Thai, Arabic — scripts that should never appear in JP/EN art exhibition text
_ANOMALOUS_RE = re.compile(r'[\u0400-\u04FF\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F\u0E00-\u0E7F\u0600-\u06FF]')


def _has_anomalous_unicode(events: list) -> list[str]:
    """Check text fields in events for unexpected scripts. Returns list of warnings."""
    warnings = []
    for i, e in enumerate(events):
        if not isinstance(e, dict):
            continue
        for field in ("title", "venue", "summary"):
            val = e.get(field, "")
            if not val:
                continue
            matches = _ANOMALOUS_RE.findall(val)
            if matches:
                chars = "".join(set(matches))
                warnings.append(f"  [UNICODE] event[{i}].{field}: found '{chars}' in: {val[:60]}")
    return warnings


def _clean_anomalous_unicode(events: list) -> list:
    """Remove anomalous unicode characters from text fields."""
    for e in events:
        if not isinstance(e, dict):
            continue
        for field in ("title", "venue", "summary"):
            val = e.get(field, "")
            if val:
                e[field] = _ANOMALOUS_RE.sub("", val)
    return events


def _load_env():
    """Load .env file if present."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


def create_client() -> OpenAI:
    _load_env()
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("Set DASHSCOPE_API_KEY in .env or environment")
    return OpenAI(
        api_key=api_key,
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    )


def _call_llm(client: OpenAI, system_prompt: str, user_msg: str) -> list:
    """Call LLM and parse JSON array response."""
    resp = client.chat.completions.create(
        model="qwen3-max",
        max_tokens=16384,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.1,
    )
    raw = resp.choices[0].message.content.strip()

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
        print(f"  [WARN] LLM returned unparseable JSON")
        print(raw[:500])
        return []


def _call_llm_with_unicode_check(client: OpenAI, system_prompt: str, user_msg: str) -> list:
    """Call LLM, check for anomalous unicode, retry if needed, clean as fallback."""
    first_result = _call_llm(client, system_prompt, user_msg)

    warnings = _has_anomalous_unicode(first_result)
    if not warnings:
        return first_result

    for w in warnings:
        print(w)

    for attempt in range(MAX_UNICODE_RETRIES):
        print(f"  [RETRY {attempt + 1}/{MAX_UNICODE_RETRIES}] unicode anomaly detected", flush=True)
        retry_result = _call_llm(client, system_prompt, user_msg)
        if not retry_result:
            # Retry returned empty/unparseable — fall through to clean first_result
            break
        retry_warnings = _has_anomalous_unicode(retry_result)
        if not retry_warnings:
            return retry_result
        for w in retry_warnings:
            print(w)

    # All retries failed or had issues — clean the best result we have
    best = first_result
    print(f"  [CLEAN] removing anomalous characters from first result", flush=True)
    return _clean_anomalous_unicode(best)


def extract_events(client: OpenAI, source_name: str, source_url: str, text: str) -> list:
    """Extract events from page text via LLM. Retries once if 0 events but content is long enough."""
    system = SYSTEM_PROMPT_TEMPLATE.format(today=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    user_msg = f"Source: {source_name}\nURL: {source_url}\n\nPage content:\n{text}"

    events = _call_llm_with_unicode_check(client, system, user_msg)

    if not events and len(text) > MIN_CONTENT_FOR_RETRY:
        print(f" [RETRY:empty]", end="", flush=True)
        events = _call_llm_with_unicode_check(client, system, user_msg)

    return events


def build_enrich_input(events: list, detail_texts: dict | None = None) -> tuple[str, str]:
    """Build LLM input for enrichment. Returns (prompt_type, user_msg)."""
    if detail_texts:
        items = []
        for i, e in enumerate(events):
            item = {"index": i, "title": e["title"], "venue": e["venue"]}
            dt = detail_texts.get(e.get("url"))
            if dt:
                item["detail"] = dt
            items.append(item)
        return "detail", json.dumps(items, ensure_ascii=False)
    else:
        items = [{"index": i, "title": e["title"], "venue": e["venue"]} for i, e in enumerate(events)]
        return "basic", json.dumps(items, ensure_ascii=False)


def enrich_events(client: OpenAI, events: list, detail_texts: dict | None = None, detail_cache: dict | None = None) -> list:
    """Enrich events with summary/recommendation via LLM."""
    if not events:
        return events

    prompt_type, user_msg = build_enrich_input(events, detail_texts)
    system_prompt = ENRICH_PROMPT_DETAIL if prompt_type == "detail" else ENRICH_PROMPT_BASIC
    enrichments = _call_llm_with_unicode_check(client, system_prompt, user_msg)

    enrich_map = {e["index"]: e for e in enrichments if isinstance(e, dict)}
    for i, event in enumerate(events):
        em = enrich_map.get(i, {})
        event["summary"] = em.get("summary", "")
        event["recommendation"] = em.get("recommendation", "normal")
        if detail_texts:
            has_detail = bool(detail_texts.get(event.get("url")))
            event["detail_fetched"] = has_detail
            if has_detail:
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

        # Populate image from detail_cache (og:image)
        if detail_cache:
            url = event.get("url")
            if url and url in detail_cache:
                img = detail_cache[url].get("image")
                if img:
                    event["image"] = img

    return events
