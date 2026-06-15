"""LLM queries via simonw/llm, Gemini CLI, or Google GenAI API."""
import os
import json
import re
import sqlite3
import subprocess
import time
import urllib.request

from gopher_lib import safe_print

LLM_MODEL = "gemini-2.5-flash"
LLM_LOCAL = "llama3.2"

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(SCRIPT_DIR, "college_gopher.db")


# --- API Layer ---

def get_api_key():
    """Read API key from environment."""
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


def _call_google_api(question):
    """Call Gemini REST API with google_search grounding. Returns (raw_text, api_response)."""
    api_key = get_api_key()
    if not api_key:
        return "ERROR: No API key found.", None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{LLM_MODEL}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": question}]}],
        "tools": [{"google_search": {}}],
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=60)
        api_response = json.loads(resp.read())
        raw = ""
        for part in api_response.get("candidates", [{}])[0].get("content", {}).get("parts", []):
            if "text" in part:
                raw += part["text"]
        return raw, api_response
    except Exception as e:
        return f"ERROR: {e}", None


def _call_gemini_cli(question):
    """Call gemini CLI. Returns (raw_text, None)."""
    try:
        result = subprocess.run(["gemini", "--skip-trust", "-p", question], capture_output=True, text=True, timeout=90)
        return result.stdout.strip(), None
    except Exception as e:
        return f"ERROR: {e}", None


def ask_llm(question, model=None):
    """Ask local LLM via simonw/llm CLI."""
    m = model or LLM_LOCAL
    for attempt in range(3):
        try:
            result = subprocess.run(
                ["python3.11", "-m", "llm", "-m", m],
                input=question, capture_output=True, text=True, timeout=90,
            )
            return result.stdout.strip()
        except Exception as e:
            if attempt == 2: return f"ERROR: {e}"
            time.sleep(5)
    return "ERROR"


# --- Grounded Cache (DB) ---

def _save_to_cache(cid, cache_type, data, raw):
    """Save a successful grounded response to the DB."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """INSERT INTO grounded_cache (cid, cache_type, data_json, raw_response, source, fetched_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(cid, cache_type) DO UPDATE SET
                 data_json=excluded.data_json, raw_response=excluded.raw_response,
                 source=excluded.source, fetched_at=excluded.fetched_at""",
            (cid, cache_type, json.dumps(data), raw, data.get("source", "")),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _load_from_cache(cid, cache_type):
    """Load last-known-good grounded response from DB."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT data_json, fetched_at FROM grounded_cache WHERE cid=? AND cache_type=?",
            (cid, cache_type),
        ).fetchone()
        conn.close()
        if row:
            data = json.loads(row["data_json"])
            data["_cached_from"] = row["fetched_at"]
            return data
    except Exception:
        pass
    return None


# --- JSON Parsing ---

def _parse_json(raw, source):
    """Parse JSON from LLM response. Detects rate limiting."""
    if "429" in raw or "RESOURCE_EXHAUSTED" in raw or "quota" in raw.lower():
        safe_print(f"  🚫 Rate limited by API. Retry later.")
        return {"raw_response": raw[:500], "source": source, "rate_limited": True}

    text = raw.replace("```json", "").replace("```", "").strip()
    # Fix common LLM JSON malformations
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        json_str = json_match.group()
        json_str = re.sub(r':\s*}', ': null}', json_str)
        json_str = re.sub(r':\s*,', ': null,', json_str)
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)
        try:
            data = json.loads(json_str)
            data["source"] = source
            return data
        except json.JSONDecodeError:
            # Greedy match failed — try finding first balanced JSON object
            depth = 0
            start = text.index('{')
            for i, ch in enumerate(text[start:], start):
                if ch == '{': depth += 1
                elif ch == '}': depth -= 1
                if depth == 0:
                    try:
                        first_obj = text[start:i+1]
                        first_obj = re.sub(r':\s*}', ': null}', first_obj)
                        first_obj = re.sub(r',\s*}', '}', first_obj)
                        first_obj = re.sub(r',\s*]', ']', first_obj)
                        data = json.loads(first_obj)
                        data["source"] = source
                        return data
                    except json.JSONDecodeError:
                        break
    return {"raw_response": raw[:500], "source": source, "parse_error": True}


# --- Grounding Validation ---

def _validate_grounded(api_response, parsed_data):
    """Check if grounded search returned real data. Returns (is_valid, reason)."""
    candidate = api_response.get("candidates", [{}])[0] if api_response else {}
    gm = candidate.get("groundingMetadata", {})

    chunks = gm.get("groundingChunks", [])
    if not chunks:
        return False, "no grounding sources"
    if not gm.get("searchEntryPoint"):
        return False, "no search entry point"

    summary = (parsed_data.get("season_summary") or parsed_data.get("raw_response") or "").lower()
    hedges = ["not yet available", "has not occurred", "not available",
              "in the future", "cannot be determined", "has not started",
              "has not yet", "no information available", "season is in the future"]
    for h in hedges:
        if h in summary:
            return False, f"hedging: {h}"

    record = str(parsed_data.get("record") or "").lower()
    if not record or record in ["null", "n/a", "none", ""]:
        return False, "no record found"

    supports = gm.get("groundingSupports", [])
    if supports:
        scores = [s.get("confidenceScores", [0])[0] for s in supports if s.get("confidenceScores")]
        if scores and sum(scores) / len(scores) < 0.5:
            return False, f"low confidence ({sum(scores)/len(scores):.2f})"

    return True, "ok"


# --- Generic Grounded Search ---

def grounded_search(cid, cache_type, prompt, tmp_dir, force=False, grounding_source="google-api", validate=None):
    """Generic grounded LLM search with caching, fallback, and validation.

    Args:
        cid: college ID
        cache_type: keys the cache files and DB (e.g. "season", "facts", "programs")
        prompt: the question to ask
        tmp_dir: where to store raw/json files
        force: bypass all caches, hit API fresh
        grounding_source: "google-api" or "gemini-cli"
        validate: optional callable(api_response, data) -> (is_valid, reason)

    Returns:
        Parsed dict with source/timestamp, or error/rate_limited dict
    """
    cache_path = os.path.join(tmp_dir, f"{cid}_llm_{cache_type}.json")
    raw_path = os.path.join(tmp_dir, f"{cid}_llm_{cache_type}_raw.txt")

    # 1. Re-parse from stored raw if available (no API call)
    if not force and os.path.exists(raw_path):
        with open(raw_path) as f:
            raw = f.read()
        if raw and not raw.startswith("ERROR") and "429" not in raw:
            data = _parse_json(raw, "google_api")
            if not data.get("parse_error") and not data.get("rate_limited"):
                data["timestamp"] = time.time()
                with open(cache_path, "w") as f:
                    json.dump(data, f, indent=2)
                return data

    # 2. Check JSON cache
    if not force and os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                data = json.loads(f.read())
            if not data.get("rate_limited") and not data.get("parse_error") and time.time() - data.get("timestamp", 0) < 86400:
                return data
        except:
            pass

    # 3. Call LLM
    if grounding_source == "gemini-cli":
        raw, api_response = _call_gemini_cli(prompt)
        source = "gemini_cli"
    else:
        raw, api_response = _call_google_api(prompt)
        source = "google_api"

    # 4. Store raw (only if not an error)
    if not raw.startswith("ERROR") and "429" not in raw:
        with open(raw_path, "w") as f:
            f.write(raw)

    # 5. Parse
    data = _parse_json(raw, source)

    # 6. Validate (if provided and parse succeeded)
    if not data.get("parse_error") and not data.get("rate_limited"):
        if validate and api_response:
            is_valid, reason = validate(api_response, data)
            if not is_valid:
                data["grounding_failed"] = True
                data["grounding_reason"] = reason
                safe_print(f"  ⚠️  Grounding validation failed: {reason}")
            else:
                _save_to_cache(cid, cache_type, data, raw)
        else:
            _save_to_cache(cid, cache_type, data, raw)

    # 7. Fall back to last-known-good on failure
    if data.get("parse_error") or data.get("rate_limited") or data.get("grounding_failed"):
        cached = _load_from_cache(cid, cache_type)
        if cached:
            safe_print(f"  ℹ️  Using last-known-good {cache_type} from {cached.get('_cached_from', '?')}")
            with open(cache_path, "w") as f:
                json.dump(cached, f, indent=2)
            return cached

    # 8. Write cache
    data["timestamp"] = time.time()
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)
    return data


# --- Public API (thin wrappers) ---

def ask_about_season(college_name, tmp_dir, cid, grounding_source="google-api", force=False, athletics_url=""):
    """Fetch 2025-26 season summary via grounded search."""
    site_hint = f" (athletics site: {athletics_url})" if athletics_url else ""
    prompt = (
        f"Search the web for {college_name}{site_hint} women's basketball 2025-26 season results. "
        f"The season ran from November 2025 through April 2026 and has ended. "
        f"Find their final record (wins-losses), conference, division (NCAA D1/D2/D3 or NAIA), "
        f"whether they made the conference tournament or national tournament, "
        f"any postseason highlights, and any players who won awards (all-conference, MVP, etc). "
        f"Answer with ONLY a JSON object, no markdown: "
        f'{{"season_summary": "", "record": "", "athletic_division": "", '
        f'"conference_tournament": true/false, "ncaa_tournament": true/false, '
        f'"postseason_detail": "", "player_awards": [], "sources": []}}'
    )
    return grounded_search(cid, "season", prompt, tmp_dir,
                           force=force, grounding_source=grounding_source,
                           validate=_validate_grounded)


def ask_about_programs(college_name, tmp_dir, cid, grounding_source="google-api", force=False):
    """Check if school has dental/health science programs — uses combined facts+programs call."""
    return ask_about_facts_and_programs(college_name, tmp_dir, cid, grounding_source=grounding_source, force=force)


def ask_about_facts(college_name, tmp_dir, cid, grounding_source="google-api", force=False, athletics_url=""):
    """Extract institutional facts — uses combined facts+programs call."""
    return ask_about_facts_and_programs(college_name, tmp_dir, cid, grounding_source=grounding_source, force=force, athletics_url=athletics_url)


def ask_about_facts_and_programs(college_name, tmp_dir, cid, grounding_source="google-api", force=False, athletics_url=""):
    """Combined facts + programs in one API call."""
    site_hint = f" (athletics site: {athletics_url})" if athletics_url else ""
    prompt = (
        f"Search the web for institutional facts about {college_name}{site_hint}. "
        f'Extract: {{"student_population": "number or null", '
        f'"undergraduate_population": "number or null", '
        f'"institution_type": "Public/Private", '
        f'"athletic_division": "NCAA D1/D2/D3/NAIA", '
        f'"city": "", "state": "abbreviation", '
        f'"founded": "year or null", '
        f'"website": "main school URL", '
        f'"mascot": "team nickname/mascot", '
        f'"abbreviation": "common abbreviation e.g. CSUSB, UCLA, or null", '
        f'"has_dental_program": true/false, '
        f'"has_health_science": true/false, '
        f'"dental_programs": ["list if any"], '
        f'"health_programs": ["list if any"]'
        f'}}. Answer with ONLY a JSON object, no markdown.'
    )
    return grounded_search(cid, "facts", prompt, tmp_dir,
                           force=force, grounding_source=grounding_source)
