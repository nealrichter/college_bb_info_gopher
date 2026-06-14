"""Wikipedia data: school summary, enrollment, programs, location."""
import json
import re
import urllib.request
import urllib.parse
import urllib.error

from gopher_lib import HEADERS, fetch_and_cache, safe_print


def wiki_search(college_name, school_domain=""):
    """Search Wikipedia for a college, return best page title."""
    # Use school domain for disambiguation (most unique identifier)
    domain_hint = school_domain.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0] if school_domain else ""
    queries = [
        domain_hint if domain_hint else f"{college_name} university",
        f"{college_name} university",
        f"{college_name} college",
        college_name,
    ]
    for query in queries:
        url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
            "action": "query", "list": "search", "srsearch": query,
            "format": "json", "srlimit": 3
        })
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())
            results = data.get("query", {}).get("search", [])
            # Pass 1: prefer results with college name in title (skip list/people articles)
            for r in results:
                title_lower = r["title"].lower()
                name_lower = college_name.lower()
                if name_lower in title_lower and not title_lower.startswith("list of"):
                    return r["title"]
            # Pass 2: institution keyword match
            for r in results:
                title_lower = r["title"].lower()
                if title_lower.startswith("list of"):
                    continue
                if any(w in title_lower for w in ["university", "college", "state", "institute", "academy"]):
                    return r["title"]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                safe_print(f"  ⚠️  Wikipedia rate limited. Waiting 30s...")
                import time
                time.sleep(30)
            continue
        except:
            continue
    return None


def wiki_page_data(title):
    """Get summary + full extract + infobox wikitext for a page title."""
    # Summary
    summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
    req = urllib.request.Request(summary_url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        summary = json.loads(resp.read()).get("extract", "")
    except:
        summary = ""

    # Full text + infobox (wikitext section 0)
    params = urllib.parse.urlencode({
        "action": "query", "titles": title,
        "prop": "extracts|revisions", "explaintext": "true",
        "rvprop": "content", "rvsection": "0", "format": "json"
    })
    url = f"https://en.wikipedia.org/w/api.php?{params}"
    req = urllib.request.Request(url, headers=HEADERS)
    full_text = ""
    wikitext = ""
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            full_text = page.get("extract", "")
            revs = page.get("revisions", [{}])
            wikitext = revs[0].get("*", "") if revs else ""
    except:
        pass

    return {"summary": summary, "full_text": full_text, "wikitext": wikitext, "title": title}


def extract_infobox_field(wikitext, field_name):
    """Extract a field from wiki infobox markup."""
    pattern = rf'\|\s*{field_name}\s*=\s*(.+?)(?:\n\||\n\}})'
    match = re.search(pattern, wikitext, re.IGNORECASE | re.DOTALL)
    if match:
        val = match.group(1).strip()
        val = re.sub(r'\[\[([^\]|]*\|)?([^\]]*)\]\]', r'\2', val)
        val = re.sub(r'\{\{[^}]*\}\}', '', val)
        val = re.sub(r'<[^>]+>', '', val)
        val = re.sub(r'https?://\S+', '', val)
        val = re.sub(r'\s+', ' ', val)
        # Take only the first meaningful chunk (before any pipe or stray markup)
        val = val.split("|")[0].strip()
        val = val.strip("' ")
        return val if len(val) < 100 else val[:100]
    return ""


def extract_school_info(wikitext):
    """Extract structured school data from infobox wikitext."""
    fields = {}
    for key, names in {
        "students": ["students", "enrollment"],
        "undergrads": ["undergrad", "undergraduates"],
        "institution_type": ["type"],
        "city": ["city"],
        "state": ["state"],
        "established": ["established", "founded"],
        "website": ["website", "url"],
        "athletics": ["athletics", "athletic_affiliations", "sporting_affiliations", "affiliations", "sports"],
    }.items():
        for name in names:
            val = extract_infobox_field(wikitext, name)
            if val:
                fields[key] = val
                break
        if key not in fields:
            fields[key] = ""
    return fields


def check_programs(full_text):
    """Check if school has dental or health science programs."""
    text_lower = full_text.lower()
    dental_keywords = ["dental", "dentistry", "oral health", "dental hygiene", "pre-dental"]
    health_keywords = ["health science", "biomedical", "biology",
                       "kinesiology", "public health", "health professions",
                       "nursing", "pre-med"]

    return {
        "has_dental": any(k in text_lower for k in dental_keywords),
        "dental_matches": [k for k in dental_keywords if k in text_lower],
        "has_health_science": any(k in text_lower for k in health_keywords),
        "health_matches": [k for k in health_keywords if k in text_lower],
    }


def check_programs_from_site(school_url, tmp_dir, cid):
    """Fetch school's academics page directly and scan for programs."""
    import os
    cache_file = os.path.join(tmp_dir, f"{cid}_academics.html")
    if os.path.exists(cache_file):
        with open(cache_file, errors="ignore") as f:
            html = f.read()
    else:
        # Try common academic page paths
        for path in ["/academics", "/programs", "/majors", "/academics/programs"]:
            url = school_url.rstrip("/") + path
            req = urllib.request.Request(url, headers=HEADERS)
            try:
                resp = urllib.request.urlopen(req, timeout=10)
                html = resp.read().decode("utf-8", errors="ignore")
                with open(cache_file, "w", errors="ignore") as f:
                    f.write(html)
                import time
                time.sleep(1)
                break
            except:
                html = ""
                continue
        if not html:
            return None

    text_lower = re.sub(r"<[^>]+>", " ", html).lower()
    dental_keywords = ["dental", "dentistry", "oral health", "dental hygiene", "pre-dental"]
    health_keywords = ["health science", "biomedical", "biology",
                       "kinesiology", "public health", "health professions",
                       "nursing", "pre-med"]
    return {
        "has_dental": any(k in text_lower for k in dental_keywords),
        "dental_matches": [k for k in dental_keywords if k in text_lower],
        "has_health_science": any(k in text_lower for k in health_keywords),
        "health_matches": [k for k in health_keywords if k in text_lower],
        "source": "school_website",
    }


def fetch_wiki_data(college_name, tmp_dir, cid, school_domain=""):
    """Full pipeline: search wiki, fetch page, extract data. Also checks school site for programs."""
    import os
    cached_path = os.path.join(tmp_dir, f"{cid}_wiki.json")
    if os.path.exists(cached_path):
        with open(cached_path) as f:
            return json.loads(f.read())

    title = wiki_search(college_name, school_domain=school_domain)
    if not title:
        return {"error": "not found", "college": college_name}

    page = wiki_page_data(title)
    info = extract_school_info(page["wikitext"])
    programs = check_programs(page["full_text"])

    # If Wikipedia didn't find dental/health programs, check the school site directly
    if not programs["has_dental"] and info.get("website"):
        site_url = info["website"]
        if not site_url.startswith("http"):
            site_url = "https://" + site_url
        site_programs = check_programs_from_site(site_url, tmp_dir, cid)
        if site_programs:
            programs = site_programs

    result = {
        "wiki_title": title,
        "summary": page["summary"][:3000],
        "school_info": info,
        "programs": programs,
    }

    with open(cached_path, "w") as f:
        json.dump(result, f, indent=2)

    return result
