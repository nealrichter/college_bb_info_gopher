# College BB Info Gopher

WBB (Women's Basketball) recruiting data aggregator. Scrapes athletics sites, fetches Wikipedia data, and queries LLMs (Llama 3.2 local + Gemini grounded search) to build comprehensive school profiles for recruiting research.

## Architecture

```
CSV imports → SQLite DB → fetch (remote) → digest (process) → summary (MD)
                ↓              ↓                  ↓                ↓
          schools table     tmp/*.html       digest table     college_summary/*.md
          (cid = key)       tmp/*.txt        (JSON blobs)     summary table (DB)
                            tmp/*.json
```

Each step is **atomic**, **stateful**, and **idempotent** — re-running skips already-completed work unless `--force` is passed. Schools are identified by `cid` (college_id), derived from the school's domain name.

## Active Files

### Pipeline Tools

| Script | Purpose |
|--------|---------|
| `wbb_college_gopher_init.py` | Initialize SQLite DB, import schools from CSVs |
| `wbb_college_gopher_fetch.py` | Download remote data (athletics pages, Wikipedia) → `tmp/` |
| `wbb_college_gopher_digest.py` | Process `tmp/` data into structured JSON in DB |
| `wbb_college_gopher_summary.py` | Generate Markdown profiles from DB → `college_summary/` |
| `wbb_college_gopher_run.py` | Driver: runs fetch → digest → summary pipeline |
| `wbb_college_gopher_status.py` | Status and error reporting (`--grounding`, `--email-ready`) |
| `wbb_college_season_summary.py` | Standalone season fetcher (Gemini CLI or Google API with grounded search) |
| `wbb_college_greeting_email.py` | Generate personalized recruiting emails from digest data |
| `wbb_college_export_emails.py` | Export emails to CSV for workflow tracking |
| `gemini_probe.py` | Diagnostic tool for testing Gemini API prompts |
| `email_template.txt` | Email template file (editable without code changes) |

### Library (`gopher_lib/`)

| Module | Purpose |
|--------|---------|
| `__init__.py` | HTTP fetching, caching, URL parsing, normalization |
| `records.py` | Win-loss record extraction from schedule pages |
| `lynx_extract.py` | Roster/coach extraction via lynx/links + local LLM |
| `llm.py` | LLM interface (simonw/llm CLI, Gemini CLI, Google API) |
| `wiki.py` | Wikipedia search + data extraction |
| `facts.py` | School facts via Gemini grounded search |

### Utility

| Script | Purpose |
|--------|---------|
| `csvcut.py` | CSV column extraction utility |

## Quick Start

```bash
# 1. Initialize the database (auto-discovers my_college_list.csv)
python3.11 wbb_college_gopher_init.py --reset

# 2. Run full pipeline for one school (by domain)
python3.11 wbb_college_gopher_run.py -s carroll.edu

# 3. Run full pipeline for all schools
python3.11 wbb_college_gopher_run.py --all

# 4. Check status and errors
python3.11 wbb_college_gopher_status.py
python3.11 wbb_college_gopher_status.py -s carroll.edu
```

## Individual Steps

```bash
# Fetch only
python3.11 wbb_college_gopher_fetch.py -s carroll.edu --force
python3.11 wbb_college_gopher_fetch.py --all -t wiki roster

# Digest only
python3.11 wbb_college_gopher_digest.py -s carroll.edu -t roster coaches --force

# Summary only
python3.11 wbb_college_gopher_summary.py -s carroll.edu --force
python3.11 wbb_college_gopher_summary.py --all

# Season summary (Google API grounded search)
python3.11 wbb_college_season_summary.py carroll -i my_college_list.csv -m google-api -v --force
```

## Directory Layout

```
├── college_gopher.db            # SQLite database (state for all steps)
├── tmp/                         # Raw downloaded data (HTML, text dumps, LLM responses)
├── college_summary/             # Final Markdown profiles (output)
├── my_college_list.csv  # Input school lists
└── gopher_lib/                  # Shared library
```

## SQLite Schema

| Table | Key | Purpose |
|-------|-----|---------|
| `schools` | `cid` | Master list: college name, URLs, division, blocked flag |
| `fetch_status` | `cid + fetch_type` | Tracks what's been downloaded |
| `digest` | `cid + digest_type` | Stores processed JSON (roster, coaches, record, wiki, season, facts, programs) |
| `summary` | `cid` | Final MD content and generation status |

## School Selection

All tools use `-s DOMAIN` to select a school by its domain (matched against `cid` or `school_url`):

```bash
python3.11 wbb_college_gopher_run.py -s carroll.edu
python3.11 wbb_college_gopher_status.py -s whitworth.edu
python3.11 wbb_college_gopher_fetch.py -s weber --force   # substring match works too
```

## Blocking Schools

```bash
sqlite3 college_gopher.db "UPDATE schools SET blocked=1 WHERE cid LIKE '%multnomah%'"
```

Blocked schools are skipped by all pipeline steps.

## Season Summary Tool

Uses Google's Generative AI REST API with the `google_search` grounding tool:

```bash
# Single school (verbose: shows prompt, response, search queries, sources)
python3.11 wbb_college_season_summary.py carroll -i my_college_list.csv --force -v

# All schools in a CSV
python3.11 wbb_college_season_summary.py -i my_college_list.csv --sleep 5

# Switch to gemini-cli
python3.11 wbb_college_season_summary.py -i my_college_list.csv -m gemini-cli
```

Output: `tmp/{cid}_llm_season.json` — picked up automatically by digest/summary steps.

## Email Generation

Generate personalized recruiting emails from digested school data:

```bash
# Check which schools are ready for email
python3.11 wbb_college_gopher_status.py --email-ready

# Generate all emails
python3.11 wbb_college_greeting_email.py --all

# One school (preview to terminal)
python3.11 wbb_college_greeting_email.py -s carroll.edu --stdout

# Custom template
python3.11 wbb_college_greeting_email.py --all -t my_template.txt
```

Requires per school: coach with email, season record, and roster (≥3 players). Edit `email_template.txt` to customize the message without touching code.

## Export to Spreadsheet

```bash
python3.11 wbb_college_export_emails.py -o coach_greeting_emails.csv
```

Produces a CSV with columns: `School Name`, `Status`, `Email Text` for workflow tracking.

## Dependencies

- Python 3.11+
- `lynx`, `links` (text-mode browser CLIs)
- `simonw/llm` with Llama 3.2 model (local extraction)
- `gemini-cli` (optional, grounded search via CLI)
- Google API key: set `GOOGLE_API_KEY=XXXX` or `GEMINI_API_KEY=XXXX` env var
