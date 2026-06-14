#!/usr/bin/env python3.11
"""Digest fetched data: process tmp/ into structured college_data/ JSON."""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gopher_lib import safe_print, normalize_pos, normalize_year
from gopher_lib.records import scrape_record
from gopher_lib.lynx_extract import extract_roster_lynx, extract_coaches_lynx
from gopher_lib.wiki import fetch_wiki_data
from gopher_lib.facts import extract_school_facts

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "college_gopher.db")
TMP_DIR = os.path.join(SCRIPT_DIR, "tmp")
DATA_DIR = os.path.join(SCRIPT_DIR, "college_data")
os.makedirs(DATA_DIR, exist_ok=True)

DIGEST_TYPES = ["record", "roster", "coaches", "wiki", "facts", "season"]


def get_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def digest_record(cid):
    """Extract win-loss record from cached schedule HTML."""
    path = os.path.join(TMP_DIR, f"{cid}_schedule.html")
    if not os.path.exists(path):
        return None, "no schedule cache"
    with open(path, errors="ignore") as f:
        html = f.read()
    if html == "404_NOT_FOUND":
        return None, "schedule 404"
    record = scrape_record(html)
    return record, None if record else "no record pattern found"


def digest_roster(cid, athletics_url):
    """Extract roster via LLM from cached text dump."""
    base_url = athletics_url.rstrip("/")
    is_presto = base_url.endswith("/wbkb")
    if is_presto:
        urls = [f"{base_url}/roster", f"{base_url}/2025-26/roster"]
    else:
        from gopher_lib import base_sport_url
        base = base_sport_url(athletics_url)
        urls = [f"{base}/roster", f"{base}/roster/2025-26"]

    roster = extract_roster_lynx(urls[0], TMP_DIR, cid)
    if not roster and len(urls) > 1:
        roster = extract_roster_lynx(urls[1], TMP_DIR, cid)
    return roster, None if roster else "0 players extracted"


def digest_coaches(cid, athletics_url):
    """Extract coaches via LLM from cached text dump."""
    base_url = athletics_url.rstrip("/")
    is_presto = base_url.endswith("/wbkb")
    if is_presto:
        urls = [f"{base_url}/coaches", f"{base_url}/2025-26/coaches"]
    else:
        from gopher_lib import base_sport_url
        base = base_sport_url(athletics_url)
        urls = [f"{base}/coaches", f"{base}/coaches/2025-26"]

    coaches = extract_coaches_lynx(urls[0], TMP_DIR, cid)
    if not coaches and len(urls) > 1:
        coaches = extract_coaches_lynx(urls[1], TMP_DIR, cid)
    return coaches, None if coaches else "0 coaches extracted"


def digest_wiki(cid, college, school_url):
    """Load wiki data from cache or re-fetch."""
    cache = os.path.join(TMP_DIR, f"{cid}_wiki.json")
    if os.path.exists(cache):
        with open(cache) as f:
            data = json.load(f)
        if data and not data.get("error"):
            return data, None
    return None, "no wiki data"


def digest_season(cid):
    """Load season summary from tmp cache."""
    cache = os.path.join(TMP_DIR, f"{cid}_llm_season.json")
    if os.path.exists(cache):
        with open(cache) as f:
            data = json.load(f)
        if data.get("rate_limited"):
            return None, "rate limited"
        if data.get("grounding_failed"):
            return None, f"grounding failed: {data.get('grounding_reason', '?')}"
        if data.get("parse_error"):
            return None, data.get("raw_response", "parse error")[:100]
        if not data.get("season_summary") and not data.get("record"):
            return None, "empty response (no season data)"
        return data, None
    return None, "not fetched yet"


def digest_facts(cid):
    """Load school facts from tmp cache."""
    cache = os.path.join(TMP_DIR, f"{cid}_llm_facts.json")
    if os.path.exists(cache):
        with open(cache) as f:
            data = json.load(f)
        if data and not data.get("parse_error") and not data.get("rate_limited"):
            return data, None
        return None, "parse error or rate limited"
    return None, "not fetched yet"


def digest_school(conn, cid, types=None, force=False):
    """Digest all requested types for a school."""
    row = conn.execute("SELECT * FROM schools WHERE cid=?", (cid,)).fetchone()
    if not row:
        safe_print(f"  ❌ {cid}: not found")
        return
    if row["blocked"]:
        safe_print(f"  [SKIP] {cid}: blocked")
        return

    college = row["college"]
    athletics_url = row["athletics_url"]
    school_url = row["school_url"] or ""
    digest_types = types or DIGEST_TYPES

    safe_print(f"  [{cid}] Digesting: {', '.join(digest_types)}...")

    for dt in digest_types:
        if not force:
            existing = conn.execute(
                "SELECT status FROM digest WHERE cid=? AND digest_type=?", (cid, dt)
            ).fetchone()
            if existing and existing["status"] == "ok":
                safe_print(f"    {dt}: cached")
                continue

        data = None
        error_msg = None

        if dt == "record":
            data, error_msg = digest_record(cid)
        elif dt == "roster":
            data, error_msg = digest_roster(cid, athletics_url)
        elif dt == "coaches":
            data, error_msg = digest_coaches(cid, athletics_url)
        elif dt == "wiki":
            data, error_msg = digest_wiki(cid, college, school_url)
        elif dt == "season":
            data, error_msg = digest_season(cid)
        elif dt == "facts":
            data, error_msg = digest_facts(cid)


        status = "ok" if data else "error"
        data_json = json.dumps(data) if data else None

        if dt == "record" and not data:
            status = "warning"

        conn.execute(
            """INSERT INTO digest (cid, digest_type, status, data_json, error_msg, digested_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(cid, digest_type) DO UPDATE SET
                 status=excluded.status, data_json=excluded.data_json,
                 error_msg=excluded.error_msg, digested_at=excluded.digested_at""",
            (cid, dt, status, data_json, error_msg),
        )
        conn.commit()

        icon = "✓" if status == "ok" else "⚠️" if status == "warning" else "❌"
        detail = ""
        if data and dt == "roster":
            detail = f"({len(data)} players)"
        elif data and dt == "coaches":
            detail = f"({len(data)} staff)"
        elif data and dt == "record":
            detail = f"({data.get('overall', '?')})"
        safe_print(f"    {dt}: {icon} {detail}{error_msg or ''}")


def main():
    parser = argparse.ArgumentParser(description="Digest fetched data into structured output")
    parser.add_argument("-s", "--school", help="School TID (or substring)")
    parser.add_argument("-n", type=int, help="Process first N schools")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("-t", "--types", nargs="+", choices=DIGEST_TYPES, help="Digest only these types")
    parser.add_argument("--force", action="store_true", help="Re-digest even if already done")
    parser.add_argument("--pending", action="store_true", help="Only schools with incomplete digest")
    parser.add_argument("--all", action="store_true", help="Process all active schools")
    args = parser.parse_args()

    conn = get_db(args.db)

    if args.all or (args.n and not args.school) or (args.pending and not args.school):
        cids = [r["cid"] for r in conn.execute("SELECT cid FROM schools WHERE blocked=0 ORDER BY cid").fetchall()]
    elif args.school:
        cids = [r["cid"] for r in conn.execute("SELECT cid FROM schools WHERE blocked=0 AND (cid LIKE ? OR school_url LIKE ?)", (f"%{args.school}%", f"%{args.school}%")).fetchall()]
    else:
        parser.print_help()
        sys.exit(1)

    if args.n:
        cids = cids[:args.n]

    if args.pending:
        types_to_check = args.types or DIGEST_TYPES
        pending_cids = []
        for cid in cids:
            for dt in types_to_check:
                if dt == "record":
                    continue  # optional
                r = conn.execute("SELECT status FROM digest WHERE cid=? AND digest_type=?", (cid, dt)).fetchone()
                if not r or r["status"] == "error":
                    pending_cids.append(cid)
                    break
        cids = pending_cids

    safe_print(f"Digest — {len(cids)} schools")
    for cid in cids:
        digest_school(conn, cid, types=args.types, force=args.force)

    conn.close()
    safe_print("\nDone.")


if __name__ == "__main__":
    main()
