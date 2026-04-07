"""LLM-based event extraction and enrichment."""
import json
import os
import re
from datetime import datetime, timezone

import anthropic

from sources import ENRICH_PROMPT_BASIC, ENRICH_PROMPT_DETAIL, SYSTEM_PROMPT_TEMPLATE

MIN_CONTENT_FOR_RETRY = 500


def create_client() -> anthropic.Anthropic:
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        raise RuntimeError("Set MINIMAX_API_KEY environment variable")
    return anthropic.Anthropic(
        api_key=api_key,
        base_url="https://api.minimaxi.com/anthropic",
    )


def _call_llm(client: anthropic.Anthropic, system_prompt: str, user_msg: str) -> list:
    """Call LLM and parse JSON array response."""
    resp = client.messages.create(
        model="MiniMax-M2.7",
        max_tokens=16384,
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
        print(f"  [WARN] LLM returned unparseable JSON")
        print(raw[:500])
        return []


def extract_events(client: anthropic.Anthropic, source_name: str, source_url: str, text: str) -> list:
    """Extract events from page text via LLM. Retries once if 0 events but content is long enough."""
    system = SYSTEM_PROMPT_TEMPLATE.format(today=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    user_msg = f"Source: {source_name}\nURL: {source_url}\n\nPage content:\n{text}"

    events = _call_llm(client, system, user_msg)

    if not events and len(text) > MIN_CONTENT_FOR_RETRY:
        print(f" [RETRY]", end="", flush=True)
        events = _call_llm(client, system, user_msg)

    return events


def enrich_events(client: anthropic.Anthropic, events: list, detail_texts: dict | None = None) -> list:
    """Enrich events with summary/recommendation via LLM."""
    if not events:
        return events

    if detail_texts:
        items = []
        for i, e in enumerate(events):
            item = {"index": i, "title": e["title"], "venue": e["venue"]}
            dt = detail_texts.get(e.get("url"))
            if dt:
                item["detail"] = dt
            items.append(item)
        enrichments = _call_llm(client, ENRICH_PROMPT_DETAIL, json.dumps(items, ensure_ascii=False))
    else:
        items = [{"index": i, "title": e["title"], "venue": e["venue"]} for i, e in enumerate(events)]
        enrichments = _call_llm(client, ENRICH_PROMPT_BASIC, json.dumps(items, ensure_ascii=False))

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

    return events
