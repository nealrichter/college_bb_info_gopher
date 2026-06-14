#!/usr/bin/env python3.11
"""Score schools and output ranked CSV. Higher score = better fit."""
import argparse
import csv
import os
import re
import sqlite3
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "college_gopher.db")

TOURNAMENT_DEPTH = {
    'first round': 2, 'second round': 4, 'round of 16': 5, 'third round': 5,
    'sweet 16': 6, 'quarterfinals': 7, 'elite eight': 8, 'semifinals': 9,
    'final four': 9, 'national champion runner-up': 10, 'national champion': 12
}


def score_school(row):
    """Compute score for one school. All positive — higher is better."""
    score = 0.0

    # 1. Roster need: graduating 6'+ posts (0-48 pts)
    posts = row['graduating_posts_6ft'] or 0
    score += posts * 12

    # 2. Win record (0-25 pts)
    wins = row['wins'] or 0
    losses = row['losses'] or 0
    total = wins + losses
    if total > 0:
        score += wins * 0.5
        score += (wins / total) * 10

    # Pre-compute tournament info (needed for division + freshman need)
    tr = (row['tournament_result'] or '').lower()
    tournament_deep = any(r in tr for r in ['elite eight', 'final four', 'semifinals', 'national champion'])

    # 3. Division level (0-10 pts)
    div = (row['division'] or '').lower()
    is_d1 = 'division i' in div and 'ii' not in div and 'iii' not in div
    is_d2 = 'd2' in div or ('division ii' in div and 'iii' not in div)
    is_naia = 'naia' in div
    is_d3 = 'd3' in div or 'division iii' in div

    # D1 sub-classification: high major = stretch, mid major = good fit
    is_d1_high_major = is_d1 and tournament_deep  # Elite Eight+ = high major
    is_d1_mid_major = is_d1 and not tournament_deep

    if is_d1_mid_major:
        score += 10
    elif is_d1_high_major:
        score += 7  # stretch — less likely to recruit freshmen
    elif is_d2 or is_naia:
        score += 6
    elif is_d3:
        score += 3

    # 4. Freshman recruiting need assessment
    # Elite D1 (deep tournament) = portal first, less likely to recruit freshmen
    # Mid-tier D1/D2 with good records = will get raided via portal, NEED freshmen
    # NAIA/D3 = always recruit freshmen
    if is_d1_high_major:
        # Elite D1 — portal first, slight penalty for freshman recruiting likelihood
        score += 2  # still good school, just less likely to need freshmen
    elif is_d1_mid_major and row['made_national_tournament']:
        # Mid-tier D1 tournament team — will get portal-raided, high freshman need
        # But high-win teams (25+) will also hit portal hard themselves
        if wins >= 25:
            score += posts * 2  # good program but portal-heavy both ways
        else:
            score += posts * 4  # extra bonus per graduating post (portal raid incoming)
    elif is_d2 and wins >= 20:
        # Strong D2 — also gets raided by D1 portal, needs freshmen
        score += posts * 3
    elif is_naia or is_d3:
        # Always recruit freshmen at these levels
        score += posts * 2

    # 5. National tournament (0-17 pts)
    if row['made_national_tournament']:
        score += 5
        for rnd, pts in sorted(TOURNAMENT_DEPTH.items(), key=lambda x: -x[1]):
            if rnd in tr:
                score += pts
                break

    # 5. Conference tournament (3 pts)
    if row['made_conf_tournament']:
        score += 3

    # 6. State/region preference (0-8 pts)
    state = (row['state'] or '').upper()
    cid = row['cid']
    college_lower = (row['college'] or '').lower()
    if state == 'CA':
        score += 8
        # Beach city bonus
        if any(x in college_lower for x in ['san diego', 'santa barbara', 'pepperdine', 'malibu']):
            score += 4
    elif state in ('UT', 'CO', 'ID'):
        score += 6  # easy to get to from home
    elif state == 'MT':
        score += 4  # close-ish

    # 7. Special schools bonus
    cid = row['cid']
    if cid == 'carroll_carrollathletics':
        score += 10  # know coach, special school
    elif cid == 'nau_nauathletics':
        score += 15  # special school
    elif cid in ('weber_weberstatesports', 'suu_suutbirds', 'utahtech_utahtechtrailblazers'):
        score += 10  # special schools - in-state/close

    # 7. Academic fit: dental (7 pts), health science (3 pts)
    if row['has_dental']:
        score += 7
    if row['has_health_science']:
        score += 3

    # 8. Tuition/cost (0-8 pts, lower = more points)
    tuition = row['tuition'] or ''
    tuition_num = re.search(r'[\d,]+', tuition.replace(',', ''))
    if tuition_num:
        t = int(tuition_num.group().replace(',', ''))
        if t < 10000:
            score += 8
        elif t < 20000:
            score += 6
        elif t < 30000:
            score += 4
        elif t < 40000:
            score += 2

    # 9. WUE eligible (5 pts)
    if row['wue_eligible']:
        score += 5

    # 10. Has football (2 pts)
    if row['has_football']:
        score += 2

    # 12. Beach proximity (0-10 pts, closer = more) — ocean beaches only (exclude landlocked states)
    beach = (row['beach_distance'] or '').lower()
    landlocked_states = {'UT', 'MT', 'ID', 'CO', 'AZ', 'NV', 'NM', 'WY', 'ND', 'SD', 'NE', 'KS', 'OK', 'AR', 'MO', 'IA', 'MN', 'WI', 'IN', 'OH', 'KY', 'TN', 'WV', 'VT'}
    if beach and 'not near' not in beach and state not in landlocked_states:
        miles_match = re.search(r'(\d+)\s*mile', beach)
        mins_match = re.search(r'(\d+)\s*min', beach)
        if mins_match:
            mins = int(mins_match.group(1))
            if mins <= 15:
                score += 10
            elif mins <= 30:
                score += 8
            elif mins <= 60:
                score += 6
            elif mins <= 120:
                score += 4
            elif mins <= 180:
                score += 2
        elif miles_match:
            miles = int(miles_match.group(1))
            if miles <= 10:
                score += 10
            elif miles <= 30:
                score += 8
            elif miles <= 60:
                score += 6
            else:
                score += 3
        else:
            score += 4  # mentioned beach but can't parse distance

    # 12. Coach contactable (3 pts)
    if row['coach_email']:
        score += 3

    # 13. Travel from SLC (0-3 pts)
    travel = (row['travel_from_slc'] or '').lower()
    if 'direct' in travel:
        score += 3
    elif '1-stop' in travel or '1 stop' in travel:
        score += 1

    return round(score, 1)


def main():
    parser = argparse.ArgumentParser(description="Score and rank schools")
    parser.add_argument("-o", "--output", default=os.path.join(SCRIPT_DIR, "school_rankings.csv"))
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--min-score", type=float, default=0, help="Minimum score to include")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("SELECT * FROM school_scores ORDER BY score_total DESC").fetchall()

    # Re-score and classify
    scored = []
    for row in rows:
        s = score_school(row)
        conn.execute("UPDATE school_scores SET score_total=? WHERE cid=?", (s, row['cid']))
        if s >= args.min_score:
            scored.append((s, row))

    scored.sort(key=lambda x: -x[0])

    # Classify schools
    SPECIAL_CIDS = {'carroll_carrollathletics', 'nau_nauathletics', 'weber_weberstatesports', 'suu_suutbirds', 'utahtech_utahtechtrailblazers', 'ucsb_ucsbgauchos', 'pepperdine_pepperdinewaves', 'pointloma_plnusealions', 'csumb_otterathletics', 'ucsc_goslugs'}
    d1_candidates = []
    d2_naia_candidates = []
    special = []

    for s, row in scored:
        cid = row['cid']
        div = (row['division'] or '').lower()
        is_d1 = 'division i' in div and 'ii' not in div and 'iii' not in div
        is_d2_naia = ('d2' in div or 'division ii' in div or 'naia' in div) and 'iii' not in div
        posts = row['graduating_posts_6ft'] or 0

        if cid in SPECIAL_CIDS:
            special.append((s, row))
        elif is_d1 and posts >= 1:
            d1_candidates.append((s, row))
        elif is_d2_naia:
            d2_naia_candidates.append((s, row))

    # Assign classifications (top N of each)
    classified = {}
    for s, row in special[:10]:
        classified[row['cid']] = 'Special School'
    for s, row in d2_naia_candidates[:15]:
        classified[row['cid']] = 'Great Fit'
    for s, row in d1_candidates[:15]:
        classified[row['cid']] = 'Likely Interest D1'

    # Update DB
    conn.execute("UPDATE school_scores SET classification=''")
    for cid, cls in classified.items():
        conn.execute("UPDATE school_scores SET classification=? WHERE cid=?", (cls, cid))

    conn.commit()
    conn.close()

    with open(args.output, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['CID', 'Score', 'Classification', 'College', 'Division', 'Record', 'Graduating Posts',
                    'Tournament', 'Dental', 'Health Sci', 'WUE', 'Beach', 'State'])
        for i, (score, row) in enumerate(scored, 1):
            record = f"{row['wins'] or '?'}-{row['losses'] or '?'}"
            cls = classified.get(row['cid'], '')
            w.writerow([
                row['cid'], score, cls, row['college'], row['division'] or '',
                record, row['graduating_posts_6ft'] or 0,
                row['tournament_result'] or '',
                'Y' if row['has_dental'] else '',
                'Y' if row['has_health_science'] else '',
                'Y' if row['wue_eligible'] else '',
                row['beach_distance'] or '',
                row['state'] or ''
            ])

    print(f"Wrote {args.output}: {len(scored)} schools ranked")
    print(f"\nTop 10:")
    for i, (score, row) in enumerate(scored[:10], 1):
        cls = classified.get(row['cid'], '')
        print(f"  {i:2}. {score:5.1f}  {row['college']:<30} {row['wins'] or 0}-{row['losses'] or 0}  posts:{row['graduating_posts_6ft'] or 0}  {cls}")

    print(f"\nClassifications:")
    print(f"  Great Fit (D2/NAIA): {len([c for c in classified.values() if c=='Great Fit'])}")
    print(f"  Likely Interest D1:  {len([c for c in classified.values() if c=='Likely Interest D1'])}")
    print(f"  Special School:      {len([c for c in classified.values() if c=='Special School'])}")


if __name__ == "__main__":
    main()
