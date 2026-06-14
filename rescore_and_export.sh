#!/bin/bash
set -x
python3.11 wbb_college_flatten_scores.py
python3.11 wbb_college_score.py
python3.11 wbb_college_gopher_summary.py --all
python3.11 wbb_college_greeting_email.py --all
python3.11 wbb_college_export_emails.py
python3.11 wbb_college_export_docx.py
echo "✓ Done: school_rankings.csv, coach_greeting_emails.csv, all_colleges_summary.docx"
