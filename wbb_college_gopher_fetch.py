#!/usr/bin/env python3.11
"""Fetch remote data for schools: roster, coaches, schedule, wiki, season, programs, facts."""
import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gopher_lib import base_sport_url, fetch_and_cache, safe_print
from gopher_lib.wiki import fetch_wiki_data
from gopher_lib.lynx_extract import _text_dump
from gopher_lib.llm import ask_about_season, ask_about_programs, grounded_search
from gopher_lib.facts import extract_school_facts

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "college_gopher.db")
TMP_DIR = os.path.join(SCRIPT_DIR, "tmp")
os.makedirs(TMP_DIR, exist_ok=True)

FETCH_TYPES = ["schedule", "roster", "coaches", "wiki", "season", "facts", "facts2"]


def get_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_athletics_page(cid, url, page_type):
    """Fetch and cache an athletics page (schedule/roster/coaches HTML)."""
    return fetch_and_cache(url, TMP_DIR, cid, page_type)


def fetch_roster_text(cid, athletics_url):
    """Fetch roster page as text dump for later digestion."""
    base = base_sport_url(athletics_url)
    is_presto = athletics_url.rstrip("/").endswith("/wbkb")

    if is_presto:
        urls = [f"{athletics_url.rstrip('/')}/roster", f"{athletics_url.rstrip('/')}/2025-26/roster"]
    else:
        urls = [f"{base}/roster", f"{base}/roster/2025-26"]

    for url in urls:
        text = _text_dump(url, TMP_DIR, cid, "roster")
        if text and "page not found" not in text.lower()[:500]:
            return url
    return None


def fetch_coaches_text(cid, athletics_url):
    """Fetch coaches page as text dump."""
    base = base_sport_url(athletics_url)
    is_presto = athletics_url.rstrip("/").endswith("/wbkb")

    if is_presto:
        urls = [f"{athletics_url.rstrip('/')}/coaches", f"{athletics_url.rstrip('/')}/2025-26/coaches"]
    else:
        urls = [f"{base}/coaches", f"{base}/coaches/2025-26"]

    for url in urls:
        text = _text_dump(url, TMP_DIR, cid, "coaches")
        if text and "page not found" not in text.lower()[:500]:
            return url
    return None


def fetch_school(conn, cid, types=None, force=False, method="google-api"):
    """Fetch all requested data types for a school."""
    row = conn.execute("SELECT * FROM schools WHERE cid=?", (cid,)).fetchone()
    if not row:
        safe_print(f"  ❌ {cid}: not found in DB")
        return
    if row["blocked"]:
        safe_print(f"  [SKIP] {cid}: blocked")
        return

    college = row["college"]
    athletics_url = row["athletics_url"]
    school_url = row["school_url"] or ""
    fetch_types = types or FETCH_TYPES

    safe_print(f"  [{cid}] Fetching: {', '.join(fetch_types)}...")

    for ft in fetch_types:
        # Check if already fetched (unless force)
        if not force:
            existing = conn.execute(
                "SELECT status FROM fetch_status WHERE cid=? AND fetch_type=?", (cid, ft)
            ).fetchone()
            if existing and existing["status"] in ("ok", "warning"):
                # For LLM types, verify the tmp file isn't rate_limited
                if ft in ("season", "facts"):
                    tmp_file = os.path.join(TMP_DIR, f"{cid}_llm_{ft}.json")
                    if os.path.exists(tmp_file):
                        with open(tmp_file) as tf:
                            try:
                                td = json.load(tf)
                                if td.get("rate_limited"):
                                    pass  # fall through to re-fetch
                                else:
                                    safe_print(f"    {ft}: cached")
                                    continue
                            except:
                                safe_print(f"    {ft}: cached")
                                continue
                    else:
                        safe_print(f"    {ft}: cached")
                        continue
                else:
                    safe_print(f"    {ft}: cached")
                    continue
            # If fetch errored but digest already has good data, skip
            if existing and existing["status"] == "error":
                digest_ok = conn.execute(
                    "SELECT status FROM digest WHERE cid=? AND digest_type=?", (cid, ft)
                ).fetchone()
                if digest_ok and digest_ok["status"] == "ok":
                    safe_print(f"    {ft}: cached (digest ok)")
                    continue
            # Check if tmp file already exists (from old tool runs)
            if not existing:
                tmp_files = {
                    "schedule": f"{cid}_schedule.html",
                    "roster": f"{cid}_roster_final.json",
                    "coaches": f"{cid}_coaches_final.json",
                    "wiki": f"{cid}_wiki.json",
                    "season": f"{cid}_llm_season.json",
                    "facts": f"{cid}_llm_facts.json",
                    "facts2": f"{cid}_llm_facts2.json",
                }
                tmp_file = os.path.join(TMP_DIR, tmp_files.get(ft, ""))
                if os.path.exists(tmp_file):
                    # Validate JSON files aren't error/rate_limited
                    valid = True
                    if tmp_file.endswith(".json"):
                        try:
                            with open(tmp_file) as tf:
                                td = json.load(tf)
                            if td.get("parse_error") or td.get("rate_limited") or td.get("error"):
                                valid = False
                        except:
                            valid = False
                    if valid:
                        conn.execute(
                            """INSERT INTO fetch_status (cid, fetch_type, status, cache_file, fetched_at)
                               VALUES (?, ?, 'ok', ?, datetime('now'))
                               ON CONFLICT(cid, fetch_type) DO UPDATE SET status='ok', fetched_at=datetime('now')""",
                            (cid, ft, tmp_file),
                        )
                        conn.commit()
                        safe_print(f"    {ft}: cached (from tmp)")
                        continue

        url = None
        cache_file = None
        status = "ok"
        error_msg = None

        try:
            if ft == "schedule":
                base = base_sport_url(athletics_url)
                url = f"{base}/schedule/2025-26"
                html = fetch_athletics_page(cid, url, "schedule")
                cache_file = os.path.join(TMP_DIR, f"{cid}_schedule.html")
                if not html:
                    status = "warning"
                    error_msg = "404 or empty"

            elif ft == "roster":
                url = fetch_roster_text(cid, athletics_url)
                cache_file = os.path.join(TMP_DIR, f"{cid}_roster_links.txt")
                if not url:
                    status = "error"
                    error_msg = "All roster URLs returned 404"

            elif ft == "coaches":
                url = fetch_coaches_text(cid, athletics_url)
                cache_file = os.path.join(TMP_DIR, f"{cid}_coaches_lynx.txt")
                if not url:
                    status = "error"
                    error_msg = "All coaches URLs returned 404"

            elif ft == "wiki":
                wiki = fetch_wiki_data(college, TMP_DIR, cid, school_domain=school_url)
                cache_file = os.path.join(TMP_DIR, f"{cid}_wiki.json")
                if not wiki or wiki.get("error"):
                    status = "error"
                    error_msg = wiki.get("error", "not found") if wiki else "empty"

            elif ft == "season":
                data = ask_about_season(college, TMP_DIR, cid, grounding_source=method, force=force, athletics_url=athletics_url)
                cache_file = os.path.join(TMP_DIR, f"{cid}_llm_season.json")
                if not data or data.get("parse_error"):
                    status = "error"
                    error_msg = "parse error or API failure"
                elif data.get("rate_limited"):
                    status = "error"
                    error_msg = "rate limited"

            elif ft == "facts":
                wiki_cache = os.path.join(TMP_DIR, f"{cid}_wiki.json")
                wiki_text = ""
                if os.path.exists(wiki_cache):
                    with open(wiki_cache) as wf:
                        wd = json.load(wf)
                    wiki_text = wd.get("summary", "") if not wd.get("error") else ""
                data = extract_school_facts(college, wiki_text, "", TMP_DIR, cid, grounding_source=method, force=force, athletics_url=athletics_url)
                cache_file = os.path.join(TMP_DIR, f"{cid}_llm_facts.json")
                if not data or data.get("parse_error"):
                    status = "error"
                    error_msg = "parse error or API failure"
                elif data.get("rate_limited"):
                    status = "error"
                    error_msg = "rate limited"

            elif ft == "facts2":
                # Requires digested roster — skip if not available
                roster_row = conn.execute("SELECT data_json FROM digest WHERE cid=? AND digest_type='roster' AND status='ok'", (cid,)).fetchone()
                if not roster_row or not roster_row["data_json"]:
                    status = "warning"
                    error_msg = "roster not digested yet"
                else:
                    roster = json.loads(roster_row["data_json"])
                    post_positions = {"F", "C", "F/C", "G/F"}
                    grad_years = {"Jr.", "Sr.", "Gr.", "R-Jr.", "R-Sr."}
                    posts = [p for p in roster if p and p.get("pos") in post_positions
                             and p.get("year") in grad_years
                             and (p.get("height", "").startswith("6") or p.get("height", "").startswith("7"))]
                    player_names = ", ".join(p["name"] for p in posts[:4]) if posts else "any notable players"

                    school_state = row["state"] or ""
                    if school_state.upper() == "UT":
                        tuition_q = "What is the yearly tuition cost for an in-state student?"
                    else:
                        tuition_q = "What is the yearly tuition cost for a student from Utah? Factor in WUE (Western Undergraduate Exchange) if applicable."

                    prompt = (
                        f"Search the web and answer these questions about {college}. "
                        f"Answer with ONLY a JSON object, no markdown:\n"
                        f"1. How close is {college} to the nearest beach? What's the drive time? If more than 3 hours away, answer \"NOT near beach\".\n"
                        f"2. Does {college} have a football team?\n"
                        f"3. What are the per-game stats for these Jr/Sr post players in women's basketball 2025-26 season: {player_names}? (points, rebounds, blocks per game)\n"
                        f"4. {tuition_q}\n"
                        f"5. How do you get to {college} from Salt Lake City, Utah? What's the nearest large airport, how many flight hops from SLC, and how far is the drive from that airport to campus?\n\n"
                        f'Format: {{"beach_distance": "X miles, Y minutes drive", "has_football": true/false, '
                        f'"player_stats": [{{"name": "", "ppg": 0, "rpg": 0, "bpg": 0}}], '
                        f'"tuition_utah_student": "$ amount per year", "wue_eligible": true/false, '
                        f'"travel_from_slc": {{"nearest_airport": "", "flights_from_slc": "direct/1-stop/2-stop", "drive_from_airport": "X miles, Y minutes"}}}}'
                    )
                    data = grounded_search(cid, "facts2", prompt, TMP_DIR, force=force, grounding_source=method)
                    cache_file = os.path.join(TMP_DIR, f"{cid}_llm_facts2.json")
                    if not data or data.get("parse_error"):
                        status = "error"
                        error_msg = "parse error or API failure"
                    elif data.get("rate_limited"):
                        status = "error"
                        error_msg = "rate limited"

        except Exception as e:
            status = "error"
            error_msg = str(e)[:200]

        conn.execute(
            """INSERT INTO fetch_status (cid, fetch_type, status, url, cache_file, error_msg, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(cid, fetch_type) DO UPDATE SET
                 status=excluded.status, url=excluded.url, cache_file=excluded.cache_file,
                 error_msg=excluded.error_msg, fetched_at=excluded.fetched_at""",
            (cid, ft, status, url, cache_file, error_msg),
        )
        conn.commit()

        icon = "✓" if status == "ok" else "⚠️" if status == "warning" else "❌"
        safe_print(f"    {ft}: {icon} {error_msg or ''}")


def main():
    parser = argparse.ArgumentParser(description="Fetch remote data for schools")
    parser.add_argument("-s", "--school", help="School TID (or substring to match)")
    parser.add_argument("-n", type=int, help="Process first N schools")
    parser.add_argument("-m", "--method", choices=["google-api", "gemini-cli"], default="google-api", help="LLM grounding method (default: google-api)")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("-t", "--types", nargs="+", choices=FETCH_TYPES, help="Fetch only these types")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if cached")
    parser.add_argument("--pending", action="store_true", help="Only schools missing requested fetch types")
    parser.add_argument("--all", action="store_true", help="Process all active schools")
    parser.add_argument("--sleep", type=int, default=2, help="Delay between schools (default: 2s)")
    args = parser.parse_args()

    conn = get_db(args.db)

    if args.all:
        cids = [r["cid"] for r in conn.execute("SELECT cid FROM schools WHERE blocked=0 ORDER BY cid").fetchall()]
    elif args.school:
        cids = [r["cid"] for r in conn.execute("SELECT cid FROM schools WHERE blocked=0 AND (cid LIKE ? OR school_url LIKE ?)", (f"%{args.school}%", f"%{args.school}%")).fetchall()]
    else:
        parser.print_help()
        sys.exit(1)

    # Filter to only schools with incomplete fetch types
    if args.pending:
        types_to_check = args.types or FETCH_TYPES
        pending_cids = []
        for cid in cids:
            for ft in types_to_check:
                if ft in ("season", "facts"):
                    tmp_file = os.path.join(TMP_DIR, f"{cid}_llm_{ft}.json")
                    if os.path.exists(tmp_file):
                        try:
                            with open(tmp_file) as tf:
                                td = json.load(tf)
                            if td.get("rate_limited") or td.get("parse_error"):
                                pending_cids.append(cid)
                                break
                            # Season must have a record to be considered complete
                            if ft == "season" and not td.get("record"):
                                pending_cids.append(cid)
                                break
                        except:
                            pending_cids.append(cid)
                            break
                    else:
                        pending_cids.append(cid)
                        break
                else:
                    r = conn.execute("SELECT status FROM fetch_status WHERE cid=? AND fetch_type=?", (cid, ft)).fetchone()
                    if not r or r["status"] == "error":
                        pending_cids.append(cid)
                        break
        cids = pending_cids

    if args.n:
        cids = cids[:args.n]

    safe_print(f"Fetch — {len(cids)} schools")
    for i, cid in enumerate(cids, 1):
        safe_print(f"\n[{i}/{len(cids)}] {cid}")
        fetch_school(conn, cid, types=args.types, force=args.force, method=args.method)
        if i < len(cids):
            time.sleep(args.sleep)

    conn.close()
    safe_print("\nDone.")


if __name__ == "__main__":
    main()
