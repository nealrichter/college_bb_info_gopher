#!/usr/bin/env python3.11
"""Extract CSV columns by position. NULL inserts empty column. Usage: csvcut.py file.csv 1 2 NULL 5"""
import csv, sys

if len(sys.argv) < 3:
    print("Usage: csvcut.py file.csv col1 col2 NULL col5 ...", file=sys.stderr)
    sys.exit(1)

cols = sys.argv[2:]
with open(sys.argv[1]) as f:
    for row in csv.reader(f):
        out = []
        for c in cols:
            if c.upper() == "NULL":
                out.append("")
            else:
                i = int(c) - 1
                out.append(row[i] if i < len(row) else "")
        print(",".join(out))
