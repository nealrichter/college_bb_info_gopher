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

    rows = []
    for f in files:
        with open(f) as fh:
            text = fh.read().strip()
        # Extract school name from Subject line
        school = ""
        for line in text.split("\n"):
            if line.startswith("Subject:") and "@" in line:
                school = line.split("@")[-1].strip()
                break
        if not school:
            school = os.path.basename(f).replace("_greeting_email.txt", "")
        rows.append((school, args.status, text))

    with open(args.output, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["School Name", "Status", "Email Text"])
        for r in rows:
            w.writerow(r)

    print(f"Wrote {args.output}: {len(rows)} emails")


if __name__ == "__main__":
    main()
