#!/usr/bin/env python3.11
"""Flatten digest data into school_scores table for scoring queries."""
import json
import os
import re
import sqlite3
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "college_gopher.db")


def parse_record(record_str):
    """Parse '19-11' or '19-11 (Overall), 10-5 (Conference)' into (w,l,cw,cl)."""
    if not record_str:
        return None, None, None, None
    # Overall
    m = re.search(r'(\d+)-(\d+)', record_str)
    wins = int(m.group(1)) if m else None
    losses = int(m.group(2)) if m else None
    # Conference
    parts = record_str.split(',')
    cw, cl = None, None
    if len(parts) > 1:
        cm = re.search(r'(\d+)-(\d+)', parts[1])
        if cm:
            cw, cl = int(cm.group(1)), int(cm.group(2))
    return wins, losses, cw, cl


def get_digest_json(conn, cid, dtype):
    r = conn.execute("SELECT data_json FROM digest WHERE cid=? AND digest_type=? AND status='ok'", (cid, dtype)).fetchone()
    if r and r[0]:
        return json.loads(r[0])
    # Fallback to grounded_cache
    r = conn.execute("SELECT data_json FROM grounded_cache WHERE cid=? AND cache_type=?", (cid, dtype)).fetchone()
    if r and r[0]:
        return json.loads(r[0])
    return None


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    schools = conn.execute("SELECT cid, college, state FROM schools WHERE blocked=0").fetchall()

    for school in schools:
        cid = school['cid']
        season = get_digest_json(conn, cid, 'season') or {}
        facts = get_digest_json(conn, cid, 'facts') or {}
        roster = get_digest_json(conn, cid, 'roster') or []
        coaches = get_digest_json(conn, cid, 'coaches') or []

        # Facts2 from tmp
        facts2_path = os.path.join(SCRIPT_DIR, "tmp", f"{cid}_llm_facts2.json")
        facts2 = {}
        if os.path.exists(facts2_path):
            with open(facts2_path) as f:
                facts2 = json.load(f)
            if facts2.get("parse_error") or facts2.get("rate_limited"):
                facts2 = {}

        # Parse record
        wins, losses, cw, cl = parse_record(season.get('record'))

        # Graduating 6'+ posts
        post_positions = {"F", "C", "F/C", "G/F"}
        grad_years = {"Jr.", "Sr.", "Gr.", "R-Jr.", "R-Sr."}
        graduating_posts = len([p for p in roster if p and p.get("pos") in post_positions
                                and p.get("year") in grad_years
                                and (p.get("height", "").startswith("6") or p.get("height", "").startswith("7"))])

        # Coach email
        coach_email = ""
        for c in coaches:
            if c and c.get("email"):
                coach_email = c["email"]
                break

        # Division
        division = season.get("athletic_division") or facts.get("athletic_division") or ""

        conn.execute("""
            INSERT INTO school_scores (cid, college, division, state, wins, losses, conf_wins, conf_losses,
                made_conf_tournament, made_national_tournament, graduating_posts_6ft, roster_size,
                has_dental, has_health_science, has_football, wue_eligible, tuition,
                beach_distance, travel_from_slc, coach_email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cid) DO UPDATE SET
                college=excluded.college, division=excluded.division, state=excluded.state,
                wins=excluded.wins, losses=excluded.losses, conf_wins=excluded.conf_wins, conf_losses=excluded.conf_losses,
                made_conf_tournament=excluded.made_conf_tournament, made_national_tournament=excluded.made_national_tournament,
                graduating_posts_6ft=excluded.graduating_posts_6ft, roster_size=excluded.roster_size,
                has_dental=excluded.has_dental, has_health_science=excluded.has_health_science,
                has_football=excluded.has_football, wue_eligible=excluded.wue_eligible,
                tuition=excluded.tuition, beach_distance=excluded.beach_distance,
                travel_from_slc=excluded.travel_from_slc, coach_email=excluded.coach_email
        """, (
            cid, school['college'], division, school['state'],
            wins, losses, cw, cl,
            1 if season.get('conference_tournament') else 0,
            1 if season.get('ncaa_tournament') else 0,
            graduating_posts, len(roster),
            1 if facts.get('has_dental_program') or facts.get('has_dental') else 0,
            1 if facts.get('has_health_science') or facts.get('has_health_path') else 0,
            1 if facts2.get('has_football') else 0,
            1 if facts2.get('wue_eligible') else 0,
            facts2.get('tuition_utah_student', ''),
            facts2.get('beach_distance', ''),
            json.dumps(facts2.get('travel_from_slc')) if facts2.get('travel_from_slc') else '',
            coach_email,
        ))

    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM school_scores").fetchone()[0]
    print(f"Populated school_scores: {count} schools")
    conn.close()


if __name__ == "__main__":
    main()
