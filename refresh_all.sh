#!/bin/bash
set -x

# Full stateful refresh pipeline
# Each step skips already-completed work unless --force is used
# Ctrl-C safe — progress is saved

# 1. Fetch all remote data (skips cached)
python3.11 wbb_college_gopher_fetch.py --all -t schedule roster coaches wiki --sleep 2

# 2. Fetch LLM grounded data (skips cached, retries rate-limited)
python3.11 wbb_college_gopher_fetch.py --all -t season facts --sleep 5

# 3. Digest all data into DB
python3.11 wbb_college_gopher_digest.py --all

# 4. Fetch facts2 (requires digested roster)
python3.11 wbb_college_gopher_fetch.py --all -t facts2 --sleep 5

# 5. Flatten scores into scoring table
python3.11 wbb_college_flatten_scores.py

# 6. Score and rank
python3.11 wbb_college_score.py

# 7. Generate summary Markdown (includes score)
python3.11 wbb_college_gopher_summary.py --all

# 8. Generate emails
python3.11 wbb_college_greeting_email.py --all

# 9. Export email CSV (sorted by score)
python3.11 wbb_college_export_emails.py

# 10. Export DOCX summary (ordered by score)
python3.11 wbb_college_export_docx.py

echo ""
echo "=========================================="
echo "✓ Full refresh complete."
echo "  school_rankings.csv"
echo "  coach_greeting_emails.csv"
echo "  all_colleges_summary.docx"
echo "  college_summary/*.md"
echo "  college_emails/*.txt"
echo "=========================================="
