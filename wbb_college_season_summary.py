#!/usr/bin/env python3.11
"""Standalone WBB season summary fetcher. Uses Gemini grounded search (CLI or API)."""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gopher_lib import team_id_from_url, safe_print

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(SCRIPT_DIR, "tmp")
os.makedirs(TMP_DIR, exist_ok=True)


VERBOSE = False


def get_api_key():
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


def fetch_season_gemini_cli(college_name):
    """Fetch season summary via gemini CLI with Google Search grounding."""
    question = (
        f"Search the web for {college_name} women's basketball 2025-26 season results. "
        f"The season ran from November 2025 through April 2026 and has ended. "
        f"Find their final record (wins-losses), conference, division (NCAA D1/D2/D3 or NAIA), "
        f"whether they made the conference tournament or national tournament, "
        f"any postseason highlights, and any players who won awards (all-conference, MVP, etc). "
        f"Answer with ONLY a JSON object, no markdown: "
        f'{{"season_summary": "", "record": "", "athletic_division": "", '
        f'"conference_tournament": true/false, "ncaa_tournament": true/false, '
        f'"postseason_detail": "", "player_awards": [], "sources": []}}'
    )
    if VERBOSE:
        safe_print(f"  [PROMPT] {question}")
    try:
        result = subprocess.run(["gemini", "-p", question], capture_output=True, text=True, timeout=90)
        raw = result.stdout.strip()
        if VERBOSE:
            safe_print(f"  [RESPONSE] {raw}")
        return raw, "gemini_cli"
    except Exception as e:
        return f"ERROR: {e}", "gemini_cli"


def fetch_season_google_api(college_name):
    """Fetch season summary via Google Generative AI REST API with search grounding."""
    import urllib.request

    api_key = get_api_key()
    if not api_key:
        return "ERROR: No API key (set GOOGLE_API_KEY or GEMINI_API_KEY)", "google_api"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    question = (
        f"Search the web for {college_name} women's basketball 2025-26 season results. "
        f"The season ran from November 2025 through April 2026 and has ended. "
        f"Find their final record (wins-losses), conference, division (NCAA D1/D2/D3 or NAIA), "
        f"whether they made the conference tournament or national tournament, "
        f"any postseason highlights, and any players who won awards (all-conference, MVP, etc). "
        f"Answer with ONLY a JSON object, no markdown: "
        f'{{"season_summary": "", "record": "", "athletic_division": "", '
        f'"conference_tournament": true/false, "ncaa_tournament": true/false, '
        f'"postseason_detail": "", "player_awards": [], "sources": []}}'
    )

    payload = {
        "contents": [{"parts": [{"text": question}]}],
        "tools": [{"google_search": {}}],
    }

    if VERBOSE:
        safe_print(f"  [PROMPT] {question}")

    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=60)
        data = json.loads(resp.read())

        # Extract text from response
        raw = ""
        for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", []):
            if "text" in part:
                raw += part["text"]

        if VERBOSE:
            safe_print(f"  [RESPONSE] {raw}")
            gm = data.get("candidates", [{}])[0].get("groundingMetadata", {})
            if gm:
                safe_print(f"  [SEARCH] {gm.get('webSearchQueries', [])}")
                for chunk in gm.get("groundingChunks", [])[:3]:
                    w = chunk.get("web", {})
                    safe_print(f"    src: {w.get('title', '?')} - {w.get('uri', '?')[:80]}")

        return raw, "google_api", data
    except Exception as e:
        return f"ERROR: {e}", "google_api", None


def validate_grounded(api_response, parsed_data):
    """Check if grounded search returned real data. Returns (is_valid, reason)."""
    candidate = api_response.get("candidates", [{}])[0] if api_response else {}
    gm = candidate.get("groundingMetadata", {})

    # No grounding sources at all
    chunks = gm.get("groundingChunks", [])
    if not chunks:
        return False, "no grounding sources"

    # Too few sources
    if len(chunks) < 2:
        return False, f"only {len(chunks)} source(s)"

    # No search entry point = search didn't trigger
    if not gm.get("searchEntryPoint"):
        return False, "no search entry point (search may not have triggered)"

    # Hedging detection
    summary = (parsed_data.get("season_summary") or "").lower()
    hedges = ["not yet available", "has not occurred", "not available",
              "in the future", "cannot be determined", "has not started",
              "has not yet", "no information available", "season is in the future"]
    for h in hedges:
        if h in summary:
            return False, f"hedging: {h}"

    # Empty record
    record = str(parsed_data.get("record") or "").lower()
    if not record or record in ["null", "n/a", "none", ""]:
        return False, "no record found"

    # Low grounding support score
    supports = gm.get("groundingSupports", [])
    if supports:
        scores = [s.get("confidenceScores", [0])[0] for s in supports if s.get("confidenceScores")]
        if scores:
            avg_confidence = sum(scores) / len(scores)
            if avg_confidence < 0.5:
                return False, f"low confidence ({avg_confidence:.2f})"

    return True, "ok"


def parse_response(raw, source):
    """Parse JSON from LLM response."""
    # Detect rate limiting
    if "429" in raw or "RESOURCE_EXHAUSTED" in raw or "quota" in raw.lower():
        return {"raw_response": raw[:500], "source": source, "rate_limited": True, "timestamp": time.time()}

    text = raw.replace("```json", "").replace("```", "").strip()
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            data["source"] = source
            data["timestamp"] = time.time()
            return data
        except json.JSONDecodeError:
            pass
    return {"raw_response": raw[:500], "source": source, "parse_error": True, "timestamp": time.time()}


def process_school(college_name, cid, method, force=False):
    """Fetch and cache season summary for one school."""
    cache_path = os.path.join(TMP_DIR, f"{cid}_llm_season.json")

    if not force and os.path.exists(cache_path):
        with open(cache_path) as f:
            data = json.load(f)
        if not data.get("parse_error") and not data.get("grounding_failed") and not data.get("rate_limited") and data.get("season_summary"):
            safe_print(f"  [{cid}] ✓ cached (use --force to re-fetch)")
            return data

    safe_print(f"  [{cid}] Fetching season summary via {method}...")

    api_response = None
    if method == "gemini-cli":
        raw, source = fetch_season_gemini_cli(college_name)
    else:
        raw, source, api_response = fetch_season_google_api(college_name)

    data = parse_response(raw, source)

    if data.get("rate_limited"):
        safe_print(f"  [{cid}] 🚫 Rate limited. Will retry on next run.")
    elif data.get("parse_error"):
        safe_print(f"  [{cid}] ❌ Parse error. Raw: {raw[:100]}...")
    else:
        # Validate grounding
        if api_response:
            is_valid, reason = validate_grounded(api_response, data)
            if not is_valid:
                data["grounding_failed"] = True
                data["grounding_reason"] = reason
                safe_print(f"  [{cid}] ⚠️  Grounding failed: {reason}")
            else:
                safe_print(f"  [{cid}] ✓ {data.get('record', '?')} ({data.get('athletic_division', '?')})")
        else:
            safe_print(f"  [{cid}] ✓ {data.get('record', '?')} ({data.get('athletic_division', '?')})")

    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)
    return data


def main():
    parser = argparse.ArgumentParser(description="WBB Season Summary Fetcher")
    parser.add_argument("school", nargs="?", help="School name or TID substring to match")
    parser.add_argument("-i", "--input", help="CSV file to process (all schools or filtered by school arg)")
    parser.add_argument("-m", "--method", choices=["gemini-cli", "google-api"], default="google-api",
                        help="Grounding method (default: google-api)")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if cached")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show prompt and response")
    parser.add_argument("--sleep", type=int, default=3, help="Seconds between requests (default: 3)")
    args = parser.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    if args.input:
        with open(args.input) as f:
            rows = list(csv.DictReader(f))
        if args.school:
            rows = [r for r in rows if args.school.lower() in r.get("College", "").lower()
                    or args.school.lower() in team_id_from_url(r.get("Link", "")).lower()]
        safe_print(f"Season Summary — {len(rows)} schools via {args.method}")
        for row in rows:
            college = row["College"].strip()
            link = row.get("Link", "").strip()
            cid = team_id_from_url(link)
            school_url = row.get("School URL", "").strip()
            school_id = team_id_from_url(school_url) if school_url else ""
            if school_id and cid and school_id != cid:
                cid = f"{school_id}_{cid}"
            if not cid:
                continue
            process_school(college, cid, args.method, force=args.force)
            time.sleep(args.sleep)
    elif args.school:
        # Direct: treat arg as college name, derive TID
        cid = args.school.lower().replace(" ", "").replace("-", "")
        process_school(args.school, cid, args.method, force=args.force)
    else:
        parser.print_help()
        sys.exit(1)

    safe_print("\nDone.")


if __name__ == "__main__":
    main()
