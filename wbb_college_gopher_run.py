#!/usr/bin/env python3.11
"""Driver: run fetch → digest → summary pipeline for one or more schools."""
import argparse
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "college_gopher.db")


def run(cmd):
    """Run a tool and stream output."""
    result = subprocess.run(cmd, cwd=SCRIPT_DIR)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Driver: fetch → digest → summary for school(s)")
    parser.add_argument("-s", "--school", help="School domain/cid substring to match")
    parser.add_argument("-n", type=int, help="Process first N schools (use with --all or -s)")
    parser.add_argument("--sleep", type=int, default=2, help="Seconds between schools (default: 2)")
    parser.add_argument("-m", "--method", choices=["google-api", "gemini-cli"], default="google-api", help="LLM grounding method (default: google-api)")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--all", action="store_true", help="Process all active schools")
    parser.add_argument("--force", action="store_true", help="Force re-run all steps")
    parser.add_argument("--step", choices=["fetch", "digest", "summary"], help="Run only one step")
    parser.add_argument("--pending", action="store_true", help="Only schools without a completed summary")
    parser.add_argument("--ls", action="store_true", help="List schools and pipeline status table")
    args = parser.parse_args()

    # --- List mode ---
    if args.ls:
        import sqlite3
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        if args.school:
            schools = conn.execute("SELECT * FROM schools WHERE blocked=0 AND (cid LIKE ? OR school_url LIKE ?) ORDER BY cid", (f"%{args.school}%", f"%{args.school}%")).fetchall()
        else:
            schools = conn.execute("SELECT * FROM schools WHERE blocked=0 ORDER BY cid").fetchall()
        if args.n:
            schools = schools[:args.n]

        fetch_types = ["schedule", "roster", "coaches", "wiki", "season", "facts"]
        digest_types = ["record", "roster", "coaches", "wiki", "season", "facts"]

        hdr = f"{'cid':<35} {'college':<25} | {'FETCH':^43} | {'DIGEST':^43} | SUM"
        sep_f = " ".join(f[:2] for f in fetch_types)
        sep_d = " ".join(f[:2] for f in digest_types)
        print(f"{'cid':<35} {'college':<25} | {sep_f} | {sep_d} | S")
        print("-" * 120)

        for s in schools:
            cid = s["cid"]
            fetch_row = ""
            for ft in fetch_types:
                r = conn.execute("SELECT status FROM fetch_status WHERE cid=? AND fetch_type=?", (cid, ft)).fetchone()
                if not r: fetch_row += " . "
                elif r["status"] == "ok": fetch_row += " ✓ "
                else: fetch_row += " ✗ "

            digest_row = ""
            for dt in digest_types:
                r = conn.execute("SELECT status FROM digest WHERE cid=? AND digest_type=?", (cid, dt)).fetchone()
                if not r: digest_row += " . "
                elif r["status"] == "ok": digest_row += " ✓ "
                else: digest_row += " ✗ "

            sm = conn.execute("SELECT status FROM summary WHERE cid=?", (cid,)).fetchone()
            sum_icon = "✓" if sm and sm["status"] == "ok" else "." if not sm else "✗"

            print(f"{cid:<35} {s['college']:<25} |{fetch_row}|{digest_row}| {sum_icon}")

        print(f"\n{len(schools)} schools")
        conn.close()
        sys.exit(0)

    if not args.school and not args.all and not args.n:
        parser.print_help()
        sys.exit(1)

    base_args = ["--db", args.db]
    if args.force:
        base_args.append("--force")
    if args.method != "google-api":
        base_args.extend(["-m", args.method])

    steps = [args.step] if args.step else ["fetch", "digest", "summary"]

    # Resolve school list from DB
    import sqlite3
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    FETCH_TYPES = ["schedule", "roster", "coaches", "wiki", "season", "facts"]
    DIGEST_TYPES = ["record", "roster", "coaches", "wiki", "season", "facts"]

    if args.all or (args.n and not args.school):
        if args.pending:
            # Schools missing any fetch (non-warning), digest, or summary
            all_cids = [r[0] for r in conn.execute("SELECT cid FROM schools WHERE blocked=0 ORDER BY cid").fetchall()]
            cids = []
            for cid in all_cids:
                # Check fetch completeness (skip schedule which is warning-ok)
                for ft in FETCH_TYPES:
                    if ft == "schedule":
                        continue
                    r = conn.execute("SELECT status FROM fetch_status WHERE cid=? AND fetch_type=?", (cid, ft)).fetchone()
                    if not r or r["status"] == "error":
                        cids.append(cid)
                        break
                else:
                    # Check digest completeness
                    for dt in DIGEST_TYPES:
                        if dt == "record":
                            continue
                        r = conn.execute("SELECT status FROM digest WHERE cid=? AND digest_type=?", (cid, dt)).fetchone()
                        if not r or r["status"] == "error":
                            cids.append(cid)
                            break
                    else:
                        # Check summary
                        r = conn.execute("SELECT status FROM summary WHERE cid=?", (cid,)).fetchone()
                        if not r or r["status"] != "ok":
                            cids.append(cid)
        else:
            cids = [r[0] for r in conn.execute("SELECT cid FROM schools WHERE blocked=0 ORDER BY cid").fetchall()]
    else:
        cids = [r[0] for r in conn.execute("SELECT cid FROM schools WHERE blocked=0 AND (cid LIKE ? OR school_url LIKE ?)", (f"%{args.school}%", f"%{args.school}%")).fetchall()]
    conn.close()

    if args.n:
        cids = cids[:args.n]

    if not cids:
        print("No matching schools found.")
        sys.exit(1)

    print(f"Pipeline: {len(cids)} schools, steps: {', '.join(steps)}")

    try:
        for i, cid in enumerate(cids):
            for step in steps:
                script = os.path.join(SCRIPT_DIR, f"wbb_college_gopher_{step}.py")
                cmd = ["python3.11", script, "-s", cid] + base_args
                if not run(cmd):
                    print(f"  ❌ {step} failed for {cid}. Continuing...")
                    break
            if i < len(cids) - 1 and args.sleep:
                time.sleep(args.sleep)
    except KeyboardInterrupt:
        print(f"\n\n⚠️  Interrupted after {i} of {len(cids)} schools. Progress saved.")

    print(f"\n{'='*50}\n✓ Pipeline complete. {len(cids)} schools processed.\n{'='*50}")


if __name__ == "__main__":
    main()
