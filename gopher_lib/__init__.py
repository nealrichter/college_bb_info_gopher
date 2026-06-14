"""Shared utilities for college_gopher: caching, fetching, common parsing."""
import os
import re
import time
import urllib.request
import threading
from urllib.parse import urlparse

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
DELAY = 2

PRINT_LOCK = threading.Lock()

def safe_print(*args, **kwargs):
    with PRINT_LOCK:
        print(*args, **kwargs)


def team_id_from_url(url):
    """Extract team ID from athletics URL domain."""
    if not url or url == "Not Found":
        return None
    netloc = urlparse(url).netloc.lower().replace("www.", "")
    # For common patterns like athletics.school.edu or go.school.edu
    parts = netloc.split(".")
    if len(parts) > 2 and parts[0] in {"athletics", "sports", "go", "www"}:
        return parts[1]
    return parts[0]


def base_sport_url(link):
    """Normalize link to sport base URL."""
    url = link.rstrip("/")
    
    # Check for a trailing year suffix (e.g. /2025-26 or /2024-25)
    year_match = re.search(r"(/20\d{2}-\d{2})$", url)
    if year_match:
        url = url[: -len(year_match.group(1))]

    for suffix in ["/roster", "/schedule", "/coaches"]:
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            
    # Check again in case it was /roster/2025-26 originally and we just stripped /roster
    year_match2 = re.search(r"(/20\d{2}-\d{2})$", url)
    if year_match2:
        url = url[: -len(year_match2.group(1))]
        
    # Normalize PrestoSports /wbkb paths to the actual page paths
    if url.endswith("/wbkb"):
        url = url.replace("/wbkb", "/womens-basketball")
    elif url.endswith("/wbb"):
        url = url.replace("/wbb", "/womens-basketball")
        
    return url


def cache_path(tmp_dir, cid, page):
    return os.path.join(tmp_dir, f"{cid}_{page}.html")


def fetch_and_cache(url, tmp_dir, cid, page):
    """Fetch URL if not cached. Returns HTML string or None. Retries on error. Handles 404s gracefully."""
    path = cache_path(tmp_dir, cid, page)
    
    if os.path.exists(path):
        with open(path, "r", errors="ignore") as f:
            content = f.read()
            if content == "404_NOT_FOUND": return None
            return content
    
    safe_print(f"    Fetching: {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    
    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            html = resp.read().decode("utf-8", errors="ignore")
            
            # Check for "Page Not Found" common indicators
            low_html = html.lower()
            if "page not found" in low_html or "404 - not found" in low_html or \
               "legacy website" in low_html or "no longer exists" in low_html:
                with open(path, "w", errors="ignore") as f:
                    f.write("404_NOT_FOUND")
                return None

            with open(path, "w", errors="ignore") as f:
                f.write(html)
            time.sleep(DELAY)
            return html
        except urllib.error.HTTPError as e:
            if e.code == 404:
                safe_print(f"    ❌ 404 Not Found: {url}")
                with open(path, "w", errors="ignore") as f:
                    f.write("404_NOT_FOUND")
                return None
            if attempt < 2:
                wait = (attempt + 1) * 5
                safe_print(f"    ⚠️  Retrying {url} in {wait}s ({e})")
                time.sleep(wait)
        except Exception as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                safe_print(f"    ⚠️  Retrying {url} in {wait}s ({e})")
                time.sleep(wait)
            else:
                safe_print(f"    ERROR fetching {url} after 3 attempts: {e}")
    return None


def html_to_text(html):
    """Strip HTML tags, collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text)


def normalize_pos(pos):
    """Normalize basketball position strings."""
    pos = str(pos or "").strip()
    pl = pos.lower()
    if "guard" in pl: return "G"
    if "forward" in pl: return "F"
    if "center" in pl: return "C"
    if "g/f" in pl or "f/g" in pl: return "G/F"
    if "f/c" in pl or "c/f" in pl: return "F/C"
    if "p" == pl or "post" in pl: return "P"
    if "w" == pl or "wing" in pl: return "W"
    return pos


def normalize_year(yr):
    """Normalize academic year strings."""
    yr = str(yr or "").strip()
    yr_map = {
        "freshman": "Fr.", "sophomore": "So.", "junior": "Jr.", "senior": "Sr.", "graduate": "Gr.",
        "fr": "Fr.", "so": "So.", "jr": "Jr.", "sr": "Sr.", "gr": "Gr."
    }
    yl = yr.lower().replace(".", "")
    if yl in yr_map:
        return yr_map[yl]
    if "redshirt" in yl:
        if "fr" in yl: return "R-Fr."
        if "so" in yl: return "R-So."
        if "jr" in yl: return "R-Jr."
        if "sr" in yl: return "R-Sr."
    return yr

def identify_graduating_posts(roster):
    """Deterministically filter Jr/Sr post players over 6ft."""
    post_positions = {"F", "C", "F/C", "G/F"}
    grad_years = {"Jr.", "Sr.", "Gr.", "R-Jr.", "R-Sr."}
    
    tall_posts = []
    for p in roster:
        pos = p.get("pos")
        ht = p.get("height", "")
        yr = p.get("year")
        
        # Height check (assumes normalized format starts with number)
        # '6-0' or '6-1' or '6'0"' -> all start with '6'
        is_tall = ht.startswith('6') or ht.startswith('7')
        
        if pos in post_positions and is_tall and yr in grad_years:
            tall_posts.append(p)
    return tall_posts
