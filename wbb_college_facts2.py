#!/usr/bin/env python3.11
"""Fetch additional facts (facts2) per school: beach proximity, football, player stats, tuition."""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gopher_lib import safe_print
from gopher_lib.llm import grounded_search

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "college_gopher.db")
TMP_DIR = os.path.join(SCRIPT_DIR, "tmp")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_post_juniors_seniors(conn, cid):
    """Get Jr/Sr 6'+ post players from roster digest."""
    r = conn.execute("SELECT data_json FROM digest WHERE cid=? AND digest_type='roster' AND status='ok'", (cid,)).fetchone()
    if not r or not r['data_json']:
        return []
    roster = json.loads(r['data_json'])
    post_positions = {"F", "C", "F/C", "G/F"}
    grad_years = {"Jr.", "Sr.", "Gr.", "R-Jr.", "R-Sr."}
    return [p for p in roster if p and p.get("pos") in post_positions
            and p.get("year") in grad_years
            and (p.get("height", "").startswith("6") or p.get("height", "").startswith("7"))]


def build_facts2_prompt(college, state, posts):
    """Build the facts2 grounded search prompt."""
    player_names = ", ".join(p["name"] for p in posts[:4]) if posts else "any notable players"

    # Determine tuition context
    if state and state.upper() == "UT":
        tuition_q = f"What is the yearly tuition cost for an in-state student?"
    else:
        tuition_q = f"What is the yearly tuition cost for a student from Utah? Factor in WUE (Western Undergraduate Exchange) if applicable."

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
    return prompt


def fetch_facts2(conn, cid, grounding_source="google-api", force=False):
    """Fetch facts2 for one school."""
    school = conn.execute("SELECT * FROM schools WHERE cid=?", (cid,)).fetchone()
    if not school or school['blocked']:
        return

    college = school['college']
    state = school['state'] or ""
    posts = get_post_juniors_seniors(conn, cid)

    prompt = build_facts2_prompt(college, state, posts)
    safe_print(f"  [{cid}] Fetching facts2...")

    data = grounded_search(cid, "facts2", prompt, TMP_DIR,
                           force=force, grounding_source=grounding_source)

    if data.get("parse_error") or data.get("rate_limited"):
        safe_print(f"    ❌ {data.get('raw_response', '')[:80]}")
    else:
        safe_print(f"    ✓ beach: {data.get('beach_distance', '?')}, football: {data.get('has_football', '?')}, tuition: {data.get('tuition_utah_student', '?')}")

    return data


def main():
    parser = argparse.ArgumentParser(description="Fetch additional facts (beach, football, player stats, tuition)")
    parser.add_argument("-s", "--school", help="School domain/cid substring")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("-n", type=int)
    parser.add_argument("-m", "--method", choices=["google-api", "gemini-cli"], default="google-api")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--sleep", type=int, default=5)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    if args.all or (args.n and not args.school):
        cids = [r[0] for r in conn.execute("SELECT cid FROM schools WHERE blocked=0 ORDER BY cid").fetchall()]
    elif args.school:
        cids = [r[0] for r in conn.execute("SELECT cid FROM schools WHERE blocked=0 AND (cid LIKE ? OR school_url LIKE ?)", (f"%{args.school}%", f"%{args.school}%")).fetchall()]
    else:
        parser.print_help()
        sys.exit(1)

    if args.n:
        cids = cids[:args.n]

    import time
    safe_print(f"Facts2 — {len(cids)} schools")
    for cid in cids:
        fetch_facts2(conn, cid, grounding_source=args.method, force=args.force)
        if len(cids) > 1:
            time.sleep(args.sleep)

    conn.close()
    safe_print("\nDone.")


if __name__ == "__main__":
    main()
