#!/usr/bin/env python3.11
"""Generate personalized greeting emails from school summary data."""
import argparse
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gopher_lib import safe_print

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "college_gopher.db")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "college_emails")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_digest(conn, cid, dtype):
    row = conn.execute("SELECT data_json FROM digest WHERE cid=? AND digest_type=? AND status='ok'", (cid, dtype)).fetchone()
    if row and row["data_json"]:
        return json.loads(row["data_json"])
    # Fallback to grounded_cache
    if dtype in ("season", "facts"):
        row = conn.execute("SELECT data_json FROM grounded_cache WHERE cid=? AND cache_type=?", (cid, dtype)).fetchone()
        if row and row["data_json"]:
            return json.loads(row["data_json"])
    return None


def identify_graduating_posts(roster):
    post_positions = {"F", "C", "F/C", "G/F"}
    grad_years = {"Jr.", "Sr.", "Gr.", "R-Jr.", "R-Sr."}
    return [p for p in roster if p and p.get("pos") in post_positions
            and (p.get("height", "").startswith("6") or p.get("height", "").startswith("7"))
            and p.get("year") in grad_years]


def synthesize_note(school, season, roster, coaches, wiki):
    """Build the personalized note about the school's program."""
    college = school["college"]

    # Mascot from facts, wiki, or season summary
    mascot = ""
    facts = get_digest(conn, cid, "facts") or {}
    if facts.get("mascot"):
        mascot = facts["mascot"]
    if not mascot:
        search_texts = [
            (wiki or {}).get("summary", ""),
            (season or {}).get("season_summary", ""),
        ]
        for text in search_texts:
            if not text:
                continue
            for phrase in ["known as the ", "teams are the ", "nicknamed the ", "mascot is the ", "called the "]:
                if phrase in text.lower():
                    idx = text.lower().index(phrase) + len(phrase)
                    mascot = text[idx:idx+30].split(".")[0].split(",")[0].split("(")[0].strip()
                    break
            if mascot:
                break

    record = (season or {}).get("record", "")
    postseason = (season or {}).get("postseason_detail", "")
    ncaa = (season or {}).get("ncaa_tournament", False)
    conf_tourney = (season or {}).get("conference_tournament", False)
    division = (season or {}).get("athletic_division", "")

    # Post players - only seniors
    tall_posts = identify_graduating_posts(roster or [])
    seniors = [p for p in tall_posts if p.get("year") in ("Sr.", "R-Sr.", "Gr.")]

    # Build note
    parts = []

    # Opening: school + record + postseason
    abbrev = facts.get("abbreviation", "")
    if abbrev and mascot:
        team_name = f"{abbrev} {mascot}"
    elif mascot:
        team_name = f"{college} {mascot}"
    elif abbrev:
        team_name = abbrev
    else:
        team_name = college

    # Determine highest tournament level
    tournament = ""
    postseason_text = (postseason or "").lower()
    if ncaa or "naia national" in postseason_text or "naia tournament" in postseason_text:
        div = (season or {}).get("athletic_division", "")
        if "NAIA" in div or "naia" in postseason_text:
            tournament = "the NAIA National Tournament"
        elif "D2" in div or "II" in div:
            tournament = "the NCAA D2 Tournament"
        elif "D3" in div or "III" in div:
            tournament = "the NCAA D3 Tournament"
        else:
            tournament = "the NCAA Tournament"
    elif conf_tourney:
        tournament = "the conference tournament"

    if record:
        line = f"I noted that the {team_name} women's basketball team finished {record} last season"
        if tournament:
            line += f", with qualification to {tournament}"
        line += "."
        parts.append(line)
        # Congrats if 17+ wins
        wins_match = re.search(r'(\d+)-\d+', record)
        if wins_match and int(wins_match.group(1)) >= 17:
            parts.append("Congrats on the season!")
    else:
        parts.append(f"I've been following the {team_name} women's basketball program.")

    # Seniors
    if seniors:
        senior_names = ", ".join(p["name"] for p in seniors[:2])
        parts.append(f"I also see that you have {len(seniors)} senior post player{'s' if len(seniors) > 1 else ''} graduating ({senior_names}). I'm looking for teams with needs in the post for fall 2027.")
    else:
        parts.append("I'm looking for programs that could use a post player for fall 2027.")

    return " ".join(parts)


def generate_email(conn, cid, template_file=None):
    """Generate email for one school."""
    school = conn.execute("SELECT * FROM schools WHERE cid=?", (cid,)).fetchone()
    if not school:
        return None, "not found"

    roster = get_digest(conn, cid, "roster") or []
    coaches = get_digest(conn, cid, "coaches") or []
    season = get_digest(conn, cid, "season")
    wiki = get_digest(conn, cid, "wiki")

    # Require minimum data to generate email
    if not coaches:
        return None, "no coaches data"
    if not season or not season.get("record"):
        return None, "no season data"
    if not roster:
        return None, "no roster data"

    # Find head coach and assistant (require email)
    head_coach = ""
    asst_coach = ""
    head_coach_lname = ""
    asst_coach_lname = ""
    head_email = ""
    asst_email = ""

    for c in coaches:
        if not c or not c.get("email"):
            continue
        title = (c.get("title") or "").lower()
        if not head_coach and ("head" in title and "women" in title or "head coach" == title):
            head_coach = c["name"]
            head_coach_lname = c["name"].split()[-1] if c["name"] else ""
            head_email = c["email"]
        elif not head_coach and "head" in title and "assistant" not in title:
            head_coach = c["name"]
            head_coach_lname = c["name"].split()[-1] if c["name"] else ""
            head_email = c["email"]
        elif ("associate" in title or "assistant" in title) and not asst_coach and head_coach:
            asst_coach = c["name"]
            asst_coach_lname = c["name"].split()[-1] if c["name"] else ""
            asst_email = c["email"]

    # Fallback: first coach with email
    if not head_coach:
        for c in coaches:
            if c and c.get("email"):
                head_coach = c["name"]
                head_coach_lname = c["name"].split()[-1] if c["name"] else ""
                head_email = c["email"]
                break

    if not head_email:
        return None, "no coach email found"

    # Deduplicate emails
    if asst_email and asst_email == head_email:
        asst_coach = ""
        asst_coach_lname = ""
        asst_email = ""

    note = synthesize_note(school, season, roster, coaches, wiki)

    # Polish via local LLM
    from gopher_lib.llm import ask_llm
    polish_prompt = (
        "Fix any spelling or grammar mistakes in this text. Keep it concise and natural. "
        "Do not change the meaning or add new information. Return only the corrected text, nothing else.\n\n"
        + note
    )
    polished = ask_llm(polish_prompt)
    if polished and not polished.startswith("ERROR") and len(polished) > 20:
        note = polished

    # Build email
    to_line = ", ".join(filter(None, [head_email, asst_email]))
    subject = f"Class of 2027 Post/Center - Sally Ball @ {school['college']}"

    greeting_names = []
    if head_coach_lname:
        greeting_names.append(head_coach_lname)
    if asst_coach_lname:
        greeting_names.append(asst_coach_lname)
    greeting = "Hi Coach " + " & Coach ".join(greeting_names) if greeting_names else "Hi Coach"

    # Load template
    template_path = template_file
    with open(template_path) as f:
        template = f.read()

    email = template.replace("$TO_EMAILS", to_line) \
                    .replace("$SCHOOL", school['college']) \
                    .replace("$GREETING", greeting) \
                    .replace("$SYNTHESIZED_NOTE", note)

    # Verify no unresolved placeholders or empty fields
    if "$" in email:
        unresolved = [w for w in email.split() if w.startswith("$")]
        return None, f"unresolved template vars: {unresolved}"
    if not to_line.strip():
        return None, "empty To line"
    if not note.strip():
        return None, "empty synthesized note"

    return email


def main():
    parser = argparse.ArgumentParser(description="Generate personalized greeting emails")
    parser.add_argument("-s", "--school", help="School domain/cid substring")
    parser.add_argument("--all", action="store_true", help="Generate for all schools")
    parser.add_argument("-n", type=int, help="Limit to N schools")
    parser.add_argument("-t", "--template", default=os.path.join(SCRIPT_DIR, "brooklyn_email_template.txt"), help="Email template file")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--stdout", action="store_true", help="Print to stdout instead of file")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    if args.all:
        cids = [r[0] for r in conn.execute("SELECT cid FROM schools WHERE blocked=0 ORDER BY cid").fetchall()]
    elif args.school:
        cids = [r[0] for r in conn.execute("SELECT cid FROM schools WHERE blocked=0 AND (cid LIKE ? OR school_url LIKE ?)", (f"%{args.school}%", f"%{args.school}%")).fetchall()]
    else:
        parser.print_help()
        sys.exit(1)

    if args.n:
        cids = cids[:args.n]

    for cid in cids:
        result = generate_email(conn, cid, template_file=args.template)
        if result is None:
            safe_print(f"  [{cid}] ❌ no data")
            continue
        if isinstance(result, tuple):
            email, reason = result
            if email is None:
                safe_print(f"  [{cid}] ⏭️  skipped: {reason}")
                continue
        else:
            email = result

        if args.stdout:
            print(f"{'='*60}")
            print(email)
        else:
            path = os.path.join(OUTPUT_DIR, f"{cid}_greeting_email.txt")
            with open(path, "w") as f:
                f.write(email)
            safe_print(f"  [{cid}] ✓ {path}")

    conn.close()
    safe_print(f"\nDone. {len(cids)} email(s).")


if __name__ == "__main__":
    main()
