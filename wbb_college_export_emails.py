#!/usr/bin/env python3.11
"""Export greeting emails to CSV: School Name, Status, Email Text."""
import argparse
import csv
import glob
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EMAILS_DIR = os.path.join(SCRIPT_DIR, "college_emails")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "coach_greeting_emails.csv")


def main():
    parser = argparse.ArgumentParser(description="Export greeting emails to CSV")
    parser.add_argument("-o", "--output", default=OUTPUT_FILE, help=f"Output CSV (default: {OUTPUT_FILE})")
    parser.add_argument("--status", default="NOT DRAFTED", help="Default status value (default: NOT DRAFTED)")
    args = parser.parse_args()

    files = sorted(glob.glob(os.path.join(EMAILS_DIR, "*_greeting_email.txt")))
    if not files:
        print("No emails found in college_emails/")
        sys.exit(1)

    # Load scores and classifications
    import sqlite3
    conn = sqlite3.connect(os.path.join(SCRIPT_DIR, "college_gopher.db"))
    scores = {}
    classifications = {}
    for r in conn.execute("SELECT cid, score_total, classification FROM school_scores").fetchall():
        scores[r[0]] = r[1] or 0
        classifications[r[0]] = r[2] or ""
    conn.close()

    # Load contact status from file
    contact_statuses = {}
    status_file = os.path.join(SCRIPT_DIR, "contact_status.csv")
    if os.path.exists(status_file):
        with open(status_file) as sf:
            for row in csv.DictReader(sf):
                contact_statuses[row['cid']] = row['status']

    rows = []
    for f in files:
        cid = os.path.basename(f).replace("_greeting_email.txt", "")
        with open(f) as fh:
            text = fh.read().strip()
        # Extract school name from Subject line
        school = ""
        for line in text.split("\n"):
            if line.startswith("Subject:") and "@" in line:
                school = line.split("@")[-1].strip()
                break
        if not school:
            school = cid
        score = scores.get(cid, 0)
        cls = classifications.get(cid, "")
        status = contact_statuses.get(cid, "") or args.status
        rows.append((cid, school, score, cls, status, text))

    # Sort by score descending
    rows.sort(key=lambda x: -x[2])

    with open(args.output, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["School ID", "School Name", "Classification", "AI Score", "Keep/Skip", "Status", "Draft-Trigger", "Email Text", "Kid Score"])
        for i, (cid, school, score, cls, status, text) in enumerate(rows, 2):
            vlookup = f"=VLOOKUP(A{i},school_rankings!A:B,2,FALSE)"
            w.writerow((cid, school, cls, vlookup, "", status, "", text, score))

    print(f"Wrote {args.output}: {len(rows)} emails")


if __name__ == "__main__":
    main()
