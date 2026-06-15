"""A/B evaluation harness: compare two OpenAI-compatible LLM backends on
IDENTICAL inputs, using the real extract/enrich prompts from this project.

Model-agnostic by design — reuse it for the NEXT model swap, no code edits:

  Reference = current production backend, read from the same env as the app
              (LLM_MODEL / LLM_BASE_URL / LLM_API_KEY, falling back to
              DASHSCOPE_API_KEY and the defaults in extract.py).
  Candidate = the model under test, read from EVAL_MODEL / EVAL_BASE_URL /
              EVAL_API_KEY. Endpoint and key default to the reference's, so
              testing a model on the same provider needs only EVAL_MODEL=<id>.

Each source's LLM input is fetched ONCE and replayed through both backends so
the only variable is the model. Detail pages are fetched once and shared.
Results are written incrementally to eval_output/<model>.json (survives crashes).

Parser sources  -> exercises the ENRICH stage (deterministic input events).
List/text sources -> exercises the EXTRACT stage (identical page text).

Usage:
  EVAL_MODEL=glm-5.1 uv run --with-requirements requirements.txt eval_models.py
  uv run --with-requirements requirements.txt eval_models.py --limit 4
  uv run --with-requirements requirements.txt eval_models.py --sources 森美術館,国立新美術館
"""
import argparse
import copy
import json
import os
import re
from pathlib import Path

from openai import OpenAI

import extract
from extract import _load_env, build_enrich_input, enrich_events, extract_events
from fetch import extract_list_text, fetch_detail_text, fetch_page
from sources import PARSERS, SOURCES

EVAL_DIR = Path("eval_output")


# --- Backend profiles ---

def reference_profile() -> dict:
    """Current production backend — resolved exactly like the app does."""
    base_url = extract._env("LLM_BASE_URL", extract.DEFAULT_BASE_URL)
    return {
        "label": extract.get_model(),
        "base_url": base_url,
        "api_key": extract._env("LLM_API_KEY") or extract._env("DASHSCOPE_API_KEY"),
        "model": extract.get_model(),
    }


def candidate_profile() -> dict:
    """Candidate under test. Endpoint and key default to the reference's, so a
    same-provider test needs only EVAL_MODEL=<id>. Override EVAL_BASE_URL /
    EVAL_API_KEY to test a different provider (e.g. bigmodel.cn or z.ai)."""
    ref = reference_profile()
    model = extract._env("EVAL_MODEL")
    if not model:
        raise RuntimeError("Set EVAL_MODEL to the candidate model id (e.g. EVAL_MODEL=glm-5.1)")
    return {
        "label": model,
        "base_url": extract._env("EVAL_BASE_URL", ref["base_url"]),
        "api_key": extract._env("EVAL_API_KEY") or ref["api_key"],
        "model": model,
    }


def make_client(profile: dict) -> OpenAI:
    if not profile["api_key"]:
        raise RuntimeError(f"Missing API key for backend '{profile['label']}'")
    # Fail loud on a stuck call instead of the SDK's default 600s x retries.
    return OpenAI(api_key=profile["api_key"], base_url=profile["base_url"],
                  timeout=180.0, max_retries=1)


def run_backend(profile: dict, fn):
    """Run fn() with the backend's model active. extract.get_model() reads
    LLM_MODEL from env at call time, so we set it around the call."""
    prev = os.environ.get("LLM_MODEL")
    os.environ["LLM_MODEL"] = profile["model"]
    try:
        return fn(make_client(profile))
    finally:
        if prev is None:
            os.environ.pop("LLM_MODEL", None)
        else:
            os.environ["LLM_MODEL"] = prev


# --- Input preparation (fetched ONCE, shared by both backends) ---

def prepare_input(src: dict, detail_cache: dict, today: str) -> dict | None:
    """Fetch and parse a source's LLM input once. Returns a dict describing the
    stage and the identical input both backends will receive."""
    name = src["name"]
    try:
        if "api" in src:
            text = fetch_page(src["api"])
        elif "parser" in src:
            text = fetch_page(src["url"])
        else:
            text = extract_list_text(fetch_page(src["url"]), src)
    except Exception as e:
        print(f"  [SKIP] {name}: fetch failed: {e}")
        return None

    if not text:
        print(f"  [SKIP] {name}: no content")
        return None

    if "parser" in src:
        events = PARSERS[src["parser"]](text, today)
        # Fetch detail pages once; both backends enrich the same events+details.
        detail_texts = None
        selector = src.get("detail_selector")
        if selector:
            detail_texts = {}
            for e in events:
                u = e.get("url")
                if not u:
                    continue
                dt = fetch_detail_text(u, selector, detail_cache)
                if dt:
                    detail_texts[u] = dt
        return {"stage": "enrich", "events": events, "detail_texts": detail_texts}

    return {"stage": "extract", "name": name, "url": src["url"], "text": text}


def run_stage(profile: dict, prepared: dict) -> list:
    """Run the prepared input through one backend. Deep-copies mutable input so
    the two backends never interfere."""
    if prepared["stage"] == "extract":
        return run_backend(profile, lambda c: extract_events(
            c, prepared["name"], prepared["url"], prepared["text"]))
    events = copy.deepcopy(prepared["events"])
    detail_texts = prepared.get("detail_texts")
    return run_backend(profile, lambda c: enrich_events(c, events, detail_texts))


# --- Diff ---

def _norm(s) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


def match_events(ref: list, cand: list) -> tuple[list, list, list]:
    """Match by normalized title. Returns (pairs, only_ref, only_cand)."""
    cand_by_title = {_norm(e.get("title")): e for e in cand}
    pairs, only_ref = [], []
    used = set()
    for r in ref:
        key = _norm(r.get("title"))
        if key in cand_by_title:
            pairs.append((r, cand_by_title[key]))
            used.add(key)
        else:
            only_ref.append(r)
    only_cand = [e for e in cand if _norm(e.get("title")) not in used]
    return pairs, only_ref, only_cand


def diff_source(name: str, stage: str, ref: list, cand: list,
                ref_err: str | None = None, cand_err: str | None = None) -> dict:
    if ref_err or cand_err:
        return {"name": name, "stage": stage, "ref_err": ref_err, "cand_err": cand_err,
                "ref_count": len(ref), "cand_count": len(cand), "matched": 0,
                "only_ref": [], "only_cand": [], "field_mismatch": {},
                "summary_missing": 0, "examples": []}
    pairs, only_ref, only_cand = match_events(ref, cand)
    fields = (["start_date", "end_date", "start_time", "end_time", "venue", "recommendation"]
              if stage == "extract"
              else ["recommendation", "venue", "start_time", "end_time", "admission", "reservation_required"])

    field_mismatch = {f: 0 for f in fields}
    summary_missing = 0
    examples = []
    for r, c in pairs:
        for f in fields:
            if (r.get(f) or None) != (c.get(f) or None):
                field_mismatch[f] += 1
                if len(examples) < 6:
                    examples.append(f"    [{f}] '{r.get('title','')[:24]}': ref={r.get(f)!r} cand={c.get(f)!r}")
        if stage == "enrich" and not (c.get("summary") or "").strip():
            summary_missing += 1

    return {
        "name": name,
        "stage": stage,
        "ref_count": len(ref),
        "cand_count": len(cand),
        "matched": len(pairs),
        "only_ref": [e.get("title") for e in only_ref],
        "only_cand": [e.get("title") for e in only_cand],
        "field_mismatch": {f: n for f, n in field_mismatch.items() if n},
        "summary_missing": summary_missing,
        "examples": examples,
    }


def print_report(diffs: list, ref_label: str, cand_label: str):
    print("\n" + "=" * 70)
    print(f"A/B REPORT  reference={ref_label}  candidate={cand_label}")
    print("=" * 70)
    tot_ref = tot_cand = tot_match = tot_mismatch = 0
    errored = []
    for d in diffs:
        if d.get("ref_err") or d.get("cand_err"):
            errored.append(d["name"])
            print(f"\n[{d['stage']:7}] {d['name']}  <-- ERROR")
            if d.get("ref_err"):
                print(f"    reference failed: {d['ref_err']}")
            if d.get("cand_err"):
                print(f"    candidate failed: {d['cand_err']}")
            continue
        miss = sum(d["field_mismatch"].values())
        tot_ref += d["ref_count"]; tot_cand += d["cand_count"]
        tot_match += d["matched"]; tot_mismatch += miss
        flag = "" if (d["ref_count"] == d["cand_count"] and not d["only_ref"]
                      and not d["only_cand"] and not miss) else "  <-- DIFF"
        print(f"\n[{d['stage']:7}] {d['name']}{flag}")
        print(f"    events: ref={d['ref_count']} cand={d['cand_count']} matched={d['matched']}")
        if d["only_ref"]:
            print(f"    only in ref ({len(d['only_ref'])}): {d['only_ref'][:4]}")
        if d["only_cand"]:
            print(f"    only in cand ({len(d['only_cand'])}): {d['only_cand'][:4]}")
        if d["field_mismatch"]:
            print(f"    field mismatches: {d['field_mismatch']}")
        if d["summary_missing"]:
            print(f"    summaries missing in candidate: {d['summary_missing']}")
        for ex in d["examples"]:
            print(ex)
    print("\n" + "-" * 70)
    print(f"TOTAL  ref_events={tot_ref}  cand_events={tot_cand}  matched={tot_match}  field_mismatches={tot_mismatch}")
    if errored:
        print(f"ERRORED sources ({len(errored)}): {errored}  <-- a backend rejected/failed these")
    print("Lower 'only in ref' = candidate didn't miss events the reference found.")
    print("Inspect full outputs in eval_output/ for summary-quality judgement.")
    print("-" * 70)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only first N sources")
    ap.add_argument("--sources", help="comma-separated source names to include")
    args = ap.parse_args()

    _load_env()
    ref_p, cand_p = reference_profile(), candidate_profile()
    print(f"reference: {ref_p['model']} @ {ref_p['base_url']}")
    print(f"candidate: {cand_p['model']} @ {cand_p['base_url']}")

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    selected = SOURCES
    if args.sources:
        wanted = {s.strip() for s in args.sources.split(",")}
        selected = [s for s in SOURCES if s["name"] in wanted]
    if args.limit:
        selected = selected[: args.limit]

    EVAL_DIR.mkdir(exist_ok=True)
    dump = {"reference": {}, "candidate": {}}

    def flush():
        """Persist after each source so a mid-run crash keeps prior results."""
        (EVAL_DIR / f"{ref_p['label']}.json").write_text(
            json.dumps(dump["reference"], ensure_ascii=False, indent=2), encoding="utf-8")
        (EVAL_DIR / f"{cand_p['label']}.json").write_text(
            json.dumps(dump["candidate"], ensure_ascii=False, indent=2), encoding="utf-8")

    def attempt(profile, prepared):
        """Run one backend; on API error record it and continue instead of crashing."""
        try:
            return run_stage(profile, prepared), None
        except Exception as e:
            msg = f"{type(e).__name__}: {str(e)[:200]}"
            print(f"    [ERROR] {profile['model']}: {msg}", flush=True)
            return [], msg

    detail_cache: dict = {}
    diffs = []
    for src in selected:
        name = src["name"]
        print(f"\nPreparing {name} ...")
        prepared = prepare_input(src, detail_cache, today)
        if not prepared:
            continue
        print(f"  running reference ({ref_p['model']}) ...", flush=True)
        ref_out, ref_err = attempt(ref_p, prepared)
        print(f"  running candidate ({cand_p['model']}) ...", flush=True)
        cand_out, cand_err = attempt(cand_p, prepared)
        dump["reference"][name] = {"_error": ref_err} if ref_err else ref_out
        dump["candidate"][name] = {"_error": cand_err} if cand_err else cand_out
        flush()
        diffs.append(diff_source(name, prepared["stage"], ref_out, cand_out, ref_err, cand_err))

    print_report(diffs, ref_p["model"], cand_p["model"])


if __name__ == "__main__":
    main()
