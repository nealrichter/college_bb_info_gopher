#!/usr/bin/env python3.11
"""Generate summary Markdown from digested data in SQLite."""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gopher_lib import safe_print

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "college_gopher.db")
SUMMARY_DIR = os.path.join(SCRIPT_DIR, "college_summary")
os.makedirs(SUMMARY_DIR, exist_ok=True)


def get_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_digest(conn, cid, digest_type):
    """Load parsed digest data for a school/type. Falls back to grounded_cache for LLM types."""
    row = conn.execute(
        "SELECT data_json FROM digest WHERE cid=? AND digest_type=? AND status='ok'",
        (cid, digest_type),
    ).fetchone()
    if row and row["data_json"]:
        return json.loads(row["data_json"])
    # Fall back to grounded_cache for LLM-sourced types
    if digest_type in ("season", "facts"):
        row = conn.execute(
            "SELECT data_json FROM grounded_cache WHERE cid=? AND cache_type=?",
            (cid, digest_type),
        ).fetchone()
        if row and row["data_json"]:
            return json.loads(row["data_json"])
    return None


def identify_graduating_posts(roster):
    post_positions = {"F", "C", "F/C", "G/F"}
    grad_years = {"Jr.", "Sr.", "Gr.", "R-Jr.", "R-Sr."}
    return [p for p in roster if p and p.get("pos") in post_positions
            and (p.get("height", "").startswith("6") or p.get("height", "").startswith("7"))
            and p.get("year") in grad_years]


def generate_md(conn, cid):
    """Generate full Markdown profile from DB digest data."""
    school = conn.execute("SELECT * FROM schools WHERE cid=?", (cid,)).fetchone()
    if not school:
        return None, "not found"

    college = school["college"]
    now = datetime.now().isoformat()

    record = get_digest(conn, cid, "record")
    roster = get_digest(conn, cid, "roster") or []
    coaches = get_digest(conn, cid, "coaches") or []
    wiki = get_digest(conn, cid, "wiki") or {}
    season = get_digest(conn, cid, "season")
    facts = get_digest(conn, cid, "facts") or {}
    programs = None  # programs data is now in facts
    si = wiki.get("school_info", {})

    # Load facts2 from tmp
    import os
    facts2_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp", f"{cid}_llm_facts2.json")
    facts2 = {}
    if os.path.exists(facts2_path):
        import json as json2
        with open(facts2_path) as f2:
            facts2 = json2.load(f2)
        if facts2.get("parse_error") or facts2.get("rate_limited"):
            facts2 = {}

    lines = []
    w = lines.append

    w(f"# {college}\n")
    w(f"**Team ID:** `{cid}`\n")

    # Score
    score_row = conn.execute("SELECT score_total, graduating_posts_6ft, tournament_result FROM school_scores WHERE cid=?", (cid,)).fetchone()
    if score_row and score_row["score_total"]:
        score_val = score_row['score_total']
        posts_val = score_row['graduating_posts_6ft'] or 0
        tourn_val = score_row['tournament_result'] or 'none'

        # Determine tier label
        div_lower = (season.get("athletic_division", "") if season else "").lower()
        if 'division i' in div_lower and 'ii' not in div_lower and 'iii' not in div_lower:
            if any(x in tourn_val.lower() for x in ['elite eight', 'final four', 'semifinal', 'champion']):
                tier = "NCAA D1 High Major (stretch)"
            else:
                tier = "NCAA D1 Mid Major"
        elif 'd2' in div_lower or 'division ii' in div_lower:
            tier = "NCAA D2"
        elif 'naia' in div_lower:
            tier = "NAIA"
        elif 'd3' in div_lower or 'division iii' in div_lower:
            tier = "NCAA D3"
        else:
            tier = div_lower or "Unknown"

        w(f"**Score:** {score_val} | **Tier:** {tier} | **Posts graduating:** {posts_val} | **Tournament:** {tourn_val}\n")

        # Classification
        cls_row = conn.execute("SELECT classification FROM school_scores WHERE cid=?", (cid,)).fetchone()
        if cls_row and cls_row["classification"]:
            w(f"**Classification:** 🏷️ {cls_row['classification']}\n")

        # LLM-generated score explanation
        from gopher_lib.llm import ask_llm
        record_str = (season or {}).get("record", "?")
        facts_data = get_digest(conn, cid, "facts") or {}
        explain_prompt = (
            f"Write 2-3 sentences explaining why {college} scored {score_val} points as a recruiting fit. "
            f"Facts: {tier} program, record {record_str}, {posts_val} graduating 6'+ post players, "
            f"tournament result: {tourn_val}, "
            f"dental programs: {'yes' if facts_data.get('has_dental_program') else 'no'}, "
            f"state: {school['state'] or '?'}, "
            f"{'D1 high major = stretch recruit (portal-first)' if 'stretch' in tier else 'good freshman recruiting fit'}. "
            f"Be concise and factual. No intro."
        )
        explanation = ask_llm(explain_prompt)
        if explanation and not explanation.startswith("ERROR"):
            w(f"\n> {explanation}\n")

    # Summary
    wiki_summary = wiki.get("summary")
    if wiki_summary:
        w(f"\n## Summary\n{wiki_summary}\n")

    # Info
    w(f"\n## Info")
    info = {
        "Location": school["location"] or f"{facts.get('city', '')}, {facts.get('state', '')}".strip(", "),
        "State": school["state"] or facts.get("state", "") or si.get("state", ""),
        "Type": facts.get("institution_type", "") or si.get("institution_type", ""),
        "Division": school["division"] or facts.get("athletic_division", "") or si.get("athletics", "") or (season.get("athletic_division", "") if season else ""),
        "Conference": school["conference"] or "",
        "Athletics": school["athletics_url"],
        "School": school["school_url"] or facts.get("website", "") or si.get("website", ""),
        "Enrollment": facts.get("student_population", "") or si.get("students", ""),
        "Undergrads": facts.get("undergraduate_population", "") or si.get("undergrads", ""),
        "Founded": facts.get("founded", "") or si.get("established", ""),
    }
    for label, val in info.items():
        if val:
            w(f"- {label}: {val}")

    # Programs
    w(f"\n## Programs")
    has_dental = facts.get("has_dental_program") or (wiki.get("programs", {}).get("has_dental"))
    has_health = facts.get("has_health_science") or (wiki.get("programs", {}).get("has_health_science"))
    if has_dental:
        dental_list = facts.get("dental_programs") or wiki.get("programs", {}).get("dental_matches", [])
        w(f"- 🦷 Dental programs: {', '.join(dental_list) if dental_list else 'yes'}")
    if has_health:
        health_list = facts.get("health_programs") or wiki.get("programs", {}).get("health_matches", [])
        w(f"- 🏥 Health science: {', '.join(health_list) if health_list else 'yes'}")
    if not has_dental and not has_health:
        if programs and not programs.get("parse_error"):
            if programs.get("has_dental") or programs.get("has_health_path"):
                w(f"- 🌐 Programs found (grounded search):")
                for p in programs.get("relevant_programs", []):
                    if isinstance(p, dict):
                        name = p.get("name", "?")
                        url = p.get("url")
                        w(f"  - [{name}]({url})" if url else f"  - {name}")
                    else:
                        w(f"  - {p}")
            else:
                w(f"- ⚠️ No dental/health programs found")
        else:
            w(f"- ⚠️ Dental/health programs: not confirmed")

    # Facts2 (lifestyle/logistics)
    if facts2:
        w(f"\n## Lifestyle & Logistics")
        if facts2.get("beach_distance"):
            w(f"- 🏖️ Beach: {facts2['beach_distance']}")
        if facts2.get("has_football") is not None:
            w(f"- 🏈 Football: {'Yes' if facts2['has_football'] else 'No'}")
        if facts2.get("tuition_utah_student"):
            school_state = (school['state'] or '').upper()
            if school_state == 'UT':
                wue = " (in-state)"
            elif facts2.get("wue_eligible"):
                wue = " (WUE eligible)"
            else:
                wue = ""
            w(f"- 💰 Tuition (UT student): {facts2['tuition_utah_student']}{wue}")
        travel = facts2.get("travel_from_slc")
        if travel and isinstance(travel, dict):
            w(f"- ✈️ From SLC: {travel.get('flights_from_slc', '?')} to {travel.get('nearest_airport', '?')}, then {travel.get('drive_from_airport', '?')}")
        elif travel:
            w(f"- ✈️ From SLC: {travel}")

    # Record
    w(f"\n## Record (2025-26)")
    if season and isinstance(season, dict) and not season.get("parse_error") and season.get("record"):
        w(f"- Record: **{season['record']}**")
        if season.get("season_summary"):
            w(f"- Season: {season['season_summary']}")
    elif record and isinstance(record, dict) and "overall" in record:
        w(f"- Overall: **{record['overall']}**")
        w(f"- Conference: **{record['conference']}**")
    if season and isinstance(season, dict) and not season.get("parse_error"):
        if season.get("conference_tournament"):
            w(f"- ✅ Conference tournament")
        if season.get("ncaa_tournament"):
            division = season.get("athletic_division", "National")
            w(f"- 🏆 {division} tournament")
        if season.get("postseason_detail"):
            w(f"- Postseason: {season['postseason_detail']}")
        if season.get("player_awards"):
            # Build name->position lookup from roster
            pos_map = {}
            for p in roster:
                if p and p.get("name"):
                    pos_map[p["name"].lower()] = p.get("pos", "")
            w(f"- 🏅 Awards:")
            for award in season["player_awards"]:
                if isinstance(award, dict):
                    name = award.get('name', award.get('player', award.get('player_name', '?')))
                    pos = pos_map.get(name.lower(), "")
                    pos_str = f" ({pos})" if pos else ""
                    w(f"  - {name}{pos_str}: {award.get('award', '?')}")
                else:
                    w(f"  - {award}")

    # Roster (exclude names that appear in coaches)
    coach_names = {c["name"].lower() for c in coaches if c}
    roster = [p for p in roster if p and p.get("name", "").lower() not in coach_names]

    # Graduating Post Players section (above roster)
    tall_posts = identify_graduating_posts(roster)
    if tall_posts:
        w(f"\n## Graduating Post Players ({len(tall_posts)} departing 6'+)")
        # Merge stats from facts2 if available
        stats_map = {}
        if facts2.get("player_stats"):
            for ps in facts2["player_stats"]:
                if isinstance(ps, dict) and ps.get("name"):
                    stats_map[ps["name"].lower()] = ps
        w(f"| Name | Pos | Ht | Yr | PPG | RPG | BPG |")
        w(f"|------|-----|-----|----|----|----|----|")
        for p in tall_posts:
            name = p['name']
            stats = stats_map.get(name.lower(), {})
            ppg = stats.get('ppg', '-')
            rpg = stats.get('rpg', '-')
            bpg = stats.get('bpg', '-')
            w(f"| **{name}** | {p['pos']} | {p['height']} | {p['year']} | {ppg} | {rpg} | {bpg} |")

    roster_url = school["athletics_url"].rstrip("/") + "/roster"
    w(f"\n## Roster ({len(roster)} players)")
    w(f"[Roster page]({roster_url})\n")
    if roster:
        w(f"| Name | Pos | Ht | Yr |")
        w(f"|------|-----|-----|----|")
        for p in roster:
            if p:
                w(f"| {p['name']} | {p['pos']} | {p['height']} | {p['year']} |")

        w(f"\n### Roster Analysis")
        posts = [p for p in roster if p and p.get("pos") in ["F", "C", "F/C", "G/F"]]
        w(f"- Total post/forward players: **{len(posts)}**")
        if tall_posts:
            w(f"- Jr/Sr 6'+ Posts: **{len(tall_posts)}**")
        else:
            w(f"- No Jr/Sr post players over 6'")

    # Coaches
    w(f"\n## Coaching Staff")
    athletics_url = school["athletics_url"] or ""
    coaches_url = athletics_url.rstrip("/") + "/coaches"
    w(f"[Coaches page]({coaches_url})\n")
    for c in coaches:
        if c:
            email = c.get("email")
            email_display = f" ({email})" if email else ""
            w(f"- **{c['name']}**: {c.get('title', '')}{email_display}")

    # Provenance
    w(f"\n---\n## Data Provenance\n")
    w(f"| Field | Source | Trust | Timestamp |")
    w(f"|------|--------|-------|----------|")
    w(f"| Record | athletics site | ✓ verified | {now} |")
    w(f"| Roster | athletics site + LLM | ✓ validated | {now} |")
    w(f"| Coaches | athletics site + LLM | ✓ validated | {now} |")
    w(f"| School info | wikipedia | ✓ verified | {now} |")
    facts_src = (facts or {}).get("source", "n/a")
    w(f"| Facts + Programs | {facts_src} + google search | 🌐 grounded | {now} |")
    season_src = (season or {}).get("source", "n/a")
    w(f"| Season | {season_src} + google search | 🌐 grounded | {now} |")

    return "\n".join(lines), None


def summarize_school(conn, cid, force=False, progress=""):
    """Generate and store summary for one school."""
    school = conn.execute("SELECT * FROM schools WHERE cid=?", (cid,)).fetchone()
    if not school:
        safe_print(f"  ❌ {cid}: not found")
        return
    if school["blocked"]:
        safe_print(f"  {progress} [SKIP] {cid}: blocked")
        return

    # Minimum data requirements
    roster = get_digest(conn, cid, "roster") or []
    coaches = get_digest(conn, cid, "coaches") or []
    if len(roster) < 3:
        safe_print(f"  {progress} [SKIP] {cid}: roster too small ({len(roster)} players)")
        return
    if not coaches:
        safe_print(f"  {progress} [SKIP] {cid}: no coaches")
        return

    md, error = generate_md(conn, cid)
    if error:
        safe_print(f"  {progress} [{cid}] ❌ {error}")
        conn.execute(
            "INSERT INTO summary (cid, status, generated_at) VALUES (?, 'error', datetime('now')) ON CONFLICT(cid) DO UPDATE SET status='error', generated_at=datetime('now')",
            (cid,),
        )
    else:
        # Write file
        md_path = os.path.join(SUMMARY_DIR, f"{cid}.md")
        with open(md_path, "w") as f:
            f.write(md)

        # Store in DB
        conn.execute(
            "INSERT INTO summary (cid, status, md_content, generated_at) VALUES (?, 'ok', ?, datetime('now')) ON CONFLICT(cid) DO UPDATE SET status='ok', md_content=excluded.md_content, generated_at=datetime('now')",
            (cid, md),
        )
        safe_print(f"  {progress} [{cid}] ✓ {md_path}")

    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Generate summary Markdown from digested data")
    parser.add_argument("-s", "--school", help="School TID (or substring)")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--force", action="store_true", help="Regenerate even if cached")
    parser.add_argument("--all", action="store_true", help="Process all active schools")
    args = parser.parse_args()

    conn = get_db(args.db)

    if args.all:
        cids = [r["cid"] for r in conn.execute("SELECT s.cid FROM schools s LEFT JOIN school_scores sc ON s.cid=sc.cid WHERE s.blocked=0 ORDER BY sc.score_total DESC, s.cid").fetchall()]
    elif args.school:
        cids = [r["cid"] for r in conn.execute("SELECT cid FROM schools WHERE blocked=0 AND (cid LIKE ? OR school_url LIKE ?)", (f"%{args.school}%", f"%{args.school}%")).fetchall()]
    else:
        parser.print_help()
        sys.exit(1)

    safe_print(f"Summary — {len(cids)} schools")
    for i, cid in enumerate(cids, 1):
        summarize_school(conn, cid, force=args.force, progress=f"[{i}/{len(cids)}]")

    conn.close()
    safe_print("\nDone.")


if __name__ == "__main__":
    main()
