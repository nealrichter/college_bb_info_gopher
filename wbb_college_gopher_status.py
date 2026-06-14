#!/usr/bin/env python3.11
"""Check status and errors across all pipeline stages."""
import argparse
import glob
import json
import os
import sqlite3
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "college_gopher.db")


def get_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def main():
    parser = argparse.ArgumentParser(description="Pipeline status and error report")
    parser.add_argument("-s", "--school", help="Show detail for one school (substring match)")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--errors", action="store_true", help="Show only errors")
    parser.add_argument("--grounding", action="store_true", help="Show grounding failures and issues")
    parser.add_argument("--email-ready", action="store_true", help="Show schools not ready for email generation")
    args = parser.parse_args()

    conn = get_db(args.db)

    if args.email_ready:
        schools = conn.execute("SELECT cid, college FROM schools WHERE blocked=0 ORDER BY cid").fetchall()
        not_ready = []
        for s in schools:
            cid = s['cid']
            # Check coaches with email
            coaches_row = conn.execute("SELECT data_json FROM digest WHERE cid=? AND digest_type='coaches' AND status='ok'", (cid,)).fetchone()
            coaches = json.loads(coaches_row['data_json']) if coaches_row and coaches_row['data_json'] else []
            has_email = any(c.get('email') for c in coaches if c)

            # Check season
            season_row = conn.execute("SELECT data_json FROM digest WHERE cid=? AND digest_type='season' AND status='ok'", (cid,)).fetchone()
            season = json.loads(season_row['data_json']) if season_row and season_row['data_json'] else None
            # Also check grounded_cache
            if not season or not season.get('record'):
                gc = conn.execute("SELECT data_json FROM grounded_cache WHERE cid=? AND cache_type='season'", (cid,)).fetchone()
                if gc:
                    season = json.loads(gc['data_json'])
            has_season = season and season.get('record')

            # Check roster
            roster_row = conn.execute("SELECT data_json FROM digest WHERE cid=? AND digest_type='roster' AND status='ok'", (cid,)).fetchone()
            roster = json.loads(roster_row['data_json']) if roster_row and roster_row['data_json'] else []
            has_roster = len(roster) >= 3

            reasons = []
            if not has_email: reasons.append("no coach email")
            if not has_season: reasons.append("no season data")
            if not has_roster: reasons.append("roster <3")
            if reasons:
                not_ready.append((cid, s['college'], ", ".join(reasons)))

        print(f"--- Not Email-Ready ({len(not_ready)} of {len(schools)}) ---\n")
        for cid, college, reason in not_ready:
            print(f"  {cid:<40} {reason}")
        print(f"\n{len(schools) - len(not_ready)} schools ARE ready for email.")
        conn.close()
        sys.exit(0)

    if args.grounding:
        print("--- Grounding Issues ---\n")
        blocked = set(r[0] for r in conn.execute("SELECT cid FROM schools WHERE blocked=1").fetchall())
        active = set(r[0] for r in conn.execute("SELECT cid FROM schools WHERE blocked=0").fetchall())
        issues = []
        for f in sorted(glob.glob(os.path.join(SCRIPT_DIR, "tmp", "*_llm_season.json"))):
            cid = os.path.basename(f).replace("_llm_season.json", "")
            if cid in blocked or cid not in active:
                continue
            with open(f) as fh:
                data = json.load(fh)
            if data.get("grounding_failed"):
                issues.append((cid, "season", f"grounding failed: {data.get('grounding_reason', '?')}"))
            elif data.get("rate_limited"):
                issues.append((cid, "season", "rate limited"))
            elif data.get("parse_error"):
                issues.append((cid, "season", "parse error"))
            elif not data.get("season_summary") and not data.get("record"):
                issues.append((cid, "season", "empty response"))
        for f in sorted(glob.glob(os.path.join(SCRIPT_DIR, "tmp", "*_llm_facts.json"))):
            cid = os.path.basename(f).replace("_llm_facts.json", "")
            if cid in blocked or cid not in active:
                continue
            with open(f) as fh:
                data = json.load(fh)
            if data.get("rate_limited"):
                issues.append((cid, "facts", "rate limited"))
            elif data.get("parse_error"):
                issues.append((cid, "facts", "parse error"))

        if issues:
            for cid, dtype, reason in issues:
                print(f"  {cid:<40} {dtype:<8} {reason}")
            print(f"\n{len(issues)} issues total")
        else:
            print("  ✓ No grounding issues found")
        conn.close()
        sys.exit(0)

    # Overview
    total = conn.execute("SELECT COUNT(*) FROM schools WHERE blocked=0").fetchone()[0]
    blocked = conn.execute("SELECT COUNT(*) FROM schools WHERE blocked=1").fetchone()[0]

    fetch_ok = conn.execute("SELECT COUNT(DISTINCT cid) FROM fetch_status WHERE status='ok'").fetchone()[0]
    fetch_err = conn.execute("SELECT COUNT(*) FROM fetch_status WHERE status='error'").fetchone()[0]

    digest_ok = conn.execute("SELECT COUNT(DISTINCT cid) FROM digest WHERE status='ok'").fetchone()[0]
    digest_err = conn.execute("SELECT COUNT(*) FROM digest WHERE status='error'").fetchone()[0]

    summary_ok = conn.execute("SELECT COUNT(*) FROM summary WHERE status='ok'").fetchone()[0]
    summary_err = conn.execute("SELECT COUNT(*) FROM summary WHERE status='error'").fetchone()[0]

    print(f"Schools: {total} active, {blocked} blocked")
    print(f"Fetch:   {fetch_ok} schools touched, {fetch_err} errors")
    print(f"Digest:  {digest_ok} schools touched, {digest_err} errors")
    print(f"Summary: {summary_ok} ok, {summary_err} errors, {total - summary_ok - summary_err} pending")
    print()

    # Detail for one school
    if args.school:
        rows = conn.execute("SELECT * FROM schools WHERE blocked=0 AND (cid LIKE ? OR school_url LIKE ?)", (f"%{args.school}%", f"%{args.school}%")).fetchall()
        for school in rows:
            cid = school["cid"]
            print(f"{'='*50}")
            print(f"{cid} ({school['college']})")
            print(f"  URL: {school['athletics_url']}")
            print(f"  Fetch:")
            for r in conn.execute("SELECT fetch_type, status, error_msg, fetched_at FROM fetch_status WHERE cid=?", (cid,)):
                icon = "✓" if r["status"] == "ok" else "❌"
                print(f"    {r['fetch_type']:12} {icon} {r['error_msg'] or ''}")
            print(f"  Digest:")
            for r in conn.execute("SELECT digest_type, status, error_msg, digested_at FROM digest WHERE cid=?", (cid,)):
                icon = "✓" if r["status"] == "ok" else "❌"
                print(f"    {r['digest_type']:12} {icon} {r['error_msg'] or ''}")
            s = conn.execute("SELECT status, generated_at FROM summary WHERE cid=?", (cid,)).fetchone()
            print(f"  Summary: {s['status'] if s else 'pending'}")
            print()
        conn.close()
        return

    # Error listing
    if args.errors or True:
        fetch_errors = conn.execute(
            "SELECT f.cid, s.college, f.fetch_type, f.error_msg FROM fetch_status f JOIN schools s ON f.cid=s.cid WHERE f.status='error' ORDER BY f.cid"
        ).fetchall()
        digest_errors = conn.execute(
            "SELECT d.cid, s.college, d.digest_type, d.error_msg FROM digest d JOIN schools s ON d.cid=s.cid WHERE d.status='error' ORDER BY d.cid"
        ).fetchall()

        if fetch_errors:
            print(f"--- Fetch Errors ({len(fetch_errors)}) ---")
            for r in fetch_errors:
                cid_val = r['cid']
                print(f"  {cid_val:35} {r['fetch_type']:12} {r['error_msg'] or ''}")
            print()

        if digest_errors:
            print(f"--- Digest Errors ({len(digest_errors)}) ---")
            for r in digest_errors:
                cid_val = r['cid']
                print(f"  {cid_val:35} {r['digest_type']:12} {r['error_msg'] or ''}")
            print()

        # Schools with no fetch at all
        no_fetch = conn.execute(
            "SELECT cid, college FROM schools WHERE blocked=0 AND cid NOT IN (SELECT DISTINCT cid FROM fetch_status)"
        ).fetchall()
        if no_fetch:
            print(f"--- Not Started ({len(no_fetch)}) ---")
            for r in no_fetch[:20]:
                cid_val = r['cid']
                college_val = r['college']
                print(f"  {cid_val:35} {college_val}")
            if len(no_fetch) > 20:
                print(f"  ... and {len(no_fetch)-20} more")
            print()

    conn.close()


if __name__ == "__main__":
    main()
