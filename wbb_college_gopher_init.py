#!/usr/bin/env python3.11
"""Initialize the college gopher SQLite database and import schools from CSVs."""
import argparse
import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gopher_lib import team_id_from_url

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "college_gopher.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS schools (
    cid TEXT PRIMARY KEY,
    college TEXT NOT NULL,
    location TEXT,
    state TEXT,
    school_url TEXT,
    athletics_url TEXT,
    conference TEXT,
    division TEXT,
    blocked INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fetch_status (
    cid TEXT NOT NULL,
    fetch_type TEXT NOT NULL,  -- roster, coaches, schedule, wiki, season, programs, facts
    status TEXT DEFAULT 'pending',  -- pending, ok, error, blocked
    url TEXT,
    cache_file TEXT,
    error_msg TEXT,
    fetched_at TEXT,
    PRIMARY KEY (cid, fetch_type),
    FOREIGN KEY (cid) REFERENCES schools(cid)
);

CREATE TABLE IF NOT EXISTS grounded_cache (
    cid TEXT NOT NULL,
    cache_type TEXT NOT NULL,  -- season, facts, programs
    data_json TEXT NOT NULL,
    raw_response TEXT,
    source TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (cid, cache_type),
    FOREIGN KEY (cid) REFERENCES schools(cid)
);

CREATE TABLE IF NOT EXISTS digest (
    cid TEXT NOT NULL,
    digest_type TEXT NOT NULL,  -- roster, coaches, record, wiki, season, programs, facts
    status TEXT DEFAULT 'pending',  -- pending, ok, error
    data_json TEXT,  -- processed JSON blob
    error_msg TEXT,
    digested_at TEXT,
    PRIMARY KEY (cid, digest_type),
    FOREIGN KEY (cid) REFERENCES schools(cid)
);

CREATE TABLE IF NOT EXISTS summary (
    cid TEXT PRIMARY KEY,
    status TEXT DEFAULT 'pending',  -- pending, ok, error
    md_content TEXT,
    generated_at TEXT,
    FOREIGN KEY (cid) REFERENCES schools(cid)
);
"""


def extract_state(location):
    if not location:
        return ""
    if "," in location:
        return location.split(",")[-1].strip()
    parts = location.strip().split()
    if len(parts) >= 2 and len(parts[-1]) == 2:
        return parts[-1]
    return ""


def compute_cid(row):
    link = row.get("Link", "").strip()
    school_url = row.get("School URL", "").strip()
    sports_id = team_id_from_url(link) if link else None
    school_id = team_id_from_url(school_url) if school_url else ""
    if school_id and sports_id and school_id != sports_id:
        return f"{school_id}_{sports_id}"
    return sports_id or "unknown"


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def import_csv(conn, csv_path):
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))

    imported = 0
    skipped = 0
    for row in rows:
        college = row.get("College", "").strip()
        link = row.get("Link", "").strip()
        if not college or not link:
            continue

        cid = compute_cid(row)
        if cid == "unknown":
            continue

        location = row.get("Location", "").strip()
        state = extract_state(location)
        school_url = row.get("School URL", "").strip()

        try:
            conn.execute(
                """INSERT INTO schools (cid, college, location, state, school_url, athletics_url)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(cid) DO UPDATE SET
                     college=excluded.college,
                     location=excluded.location,
                     state=excluded.state,
                     school_url=excluded.school_url,
                     athletics_url=excluded.athletics_url,
                     updated_at=datetime('now')""",
                (cid, college, location, state, school_url, link),
            )
            imported += 1
        except Exception as e:
            print(f"  ERROR: {cid}: {e}")
            skipped += 1

    conn.commit()
    return imported, skipped


def main():
    parser = argparse.ArgumentParser(description="Initialize college gopher DB and import CSVs")
    parser.add_argument("csvs", nargs="*", help="CSV files to import")
    parser.add_argument("--db", default=DB_PATH, help=f"Database path (default: {DB_PATH})")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate all tables")
    args = parser.parse_args()

    if args.reset and os.path.exists(args.db):
        os.remove(args.db)
        print(f"Removed existing database: {args.db}")

    conn = init_db(args.db)
    print(f"Database: {args.db}")

    # Create output dirs
    for d in ["tmp", "college_data", "college_summary"]:
        os.makedirs(os.path.join(SCRIPT_DIR, d), exist_ok=True)

    if not args.csvs:
        # Auto-discover CSVs
        args.csvs = sorted([f for f in os.listdir(SCRIPT_DIR) if f.startswith("my_college_list") and f.endswith(".csv")])

    total_imported = 0
    for csv_file in args.csvs:
        path = csv_file if os.path.isabs(csv_file) else os.path.join(SCRIPT_DIR, csv_file)
        if not os.path.exists(path):
            print(f"  SKIP: {csv_file} not found")
            continue
        imported, skipped = import_csv(conn, path)
        print(f"  {csv_file}: {imported} imported, {skipped} skipped")
        total_imported += imported

    # Show totals
    count = conn.execute("SELECT COUNT(*) FROM schools WHERE blocked=0").fetchone()[0]
    blocked = conn.execute("SELECT COUNT(*) FROM schools WHERE blocked=1").fetchone()[0]
    print(f"\nTotal active schools: {count} (blocked: {blocked})")

    conn.close()


if __name__ == "__main__":
    main()
