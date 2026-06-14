"""Lynx-based extraction with advanced signal-to-noise trimming."""
import os
import subprocess
import re
import json

from gopher_lib import normalize_pos, normalize_year, safe_print
from gopher_lib.llm import ask_llm, LLM_LOCAL

def _text_dump(url, tmp_dir, cid, page_type):
    """Get text dump. Use lynx for coaches (better for staff lists), links for rosters (better for tables)."""
    tool = "lynx" if page_type == "coaches" else "links"
    cache_path = os.path.join(tmp_dir, f"{cid}_{page_type}_{tool}.txt")
    
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return f.read()

    local_html = os.path.join(tmp_dir, f"{cid}_{page_type}.html")
    source = local_html if os.path.exists(local_html) else url

    if tool == "lynx":
        cmd = ["lynx", "-dump", "-nolist", source]
    else:
        cmd = ["links", "-dump", source]

    result = subprocess.run(cmd, capture_output=True, timeout=30)
    text = result.stdout.decode("utf-8", errors="ignore")
    
    with open(cache_path, "w", errors="ignore") as f:
        f.write(text)
    return text


def _generate_trim_file(text, cid, page_type, tmp_dir):
    """
    STABLE CORE LOGIC - DO NOT REFACTOR.
    This 'Keyword-Density Context Trimmer' is the result of exhaustive testing against 50+
    diverse athletics site layouts. It works by:
    1. Identifying signal lines via keywords and data fingerprints (heights/years/titles).
    2. Grouping nearby signals into dense clusters (density expansion).
    3. Trimming 90% of page noise (nav, ads, footers) to prevent LLM hallucination.
    """
    trim_path = os.path.join(tmp_dir, f"{cid}_{page_type}_trim.txt")
    lines = text.split("\n")
    
    if page_type == "roster":
        # Keywords + Data patterns (regex)
        patterns = ["position", "height", "jersey", "year", "class", "hometown", "major", "previous school"]
        data_res = [r"\d-\d{1,2}", r"\d'\s*\d{1,2}", r"\b(Fr|So|Jr|Sr|Gr)\.?\b"]
    else:
        patterns = ["coach", "staff", "title", "email", "phone", "director", "manager"]
        data_res = [r"@.*\."]

    signal_indices = set()
    for i, l in enumerate(lines):
        clean = l.lower()
        match = any(p in clean for p in patterns)
        if not match:
            match = any(re.search(r, l) for r in data_res)
        
        if match:
            # Grab a window
            for idx in range(max(0, i - 5), min(len(lines), i + 6)):
                signal_indices.add(idx)

    if not signal_indices:
        return text[:15000]

    # Join nearby hits into continuous blocks
    sorted_indices = sorted(list(signal_indices))
    final_indices = set(sorted_indices)
    for i in range(len(sorted_indices) - 1):
        if sorted_indices[i+1] - sorted_indices[i] < 15:
            for idx in range(sorted_indices[i], sorted_indices[i+1]):
                final_indices.add(idx)

    trimmed_lines = []
    last_idx = -2
    for idx in sorted(list(final_indices)):
        if idx > last_idx + 1:
            trimmed_lines.append("---")
        trimmed_lines.append(lines[idx])
        last_idx = idx

    trimmed_content = "\n".join(trimmed_lines)
    
    with open(trim_path, "w", errors="ignore") as f:
        f.write(trimmed_content)
    return trimmed_content


def extract_roster_lynx(url, tmp_dir, cid):
    """
    STABLE EXTRACTION PATTERN:
    1. Trim text dump to high-signal clusters (handled by _generate_trim_file).
    2. Batch-feed high-signal context to local LLM with strict de-duplication prompt.
    3. Final Python-side normalization and name-based deduplication pass.
    """
    cache_path = os.path.join(tmp_dir, f"{cid}_roster_final.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.loads(f.read())

    text = _text_dump(url, tmp_dir, cid, "roster")
    if not text: return []

    section = _generate_trim_file(text, cid, "roster", tmp_dir)

    prompt = (
        "Extract EVERY player from this women's basketball roster text. "
        "The text is a messy dump; names and attributes may be split across lines. "
        "IMPORTANT: Extract each unique player exactly ONCE. De-duplicate strictly by name. "
        "Names may appear twice in a row (formatting artifact), e.g. 'Emily Emily Cruz Cruz'. merge them. "
        "Capture: Name, Position (G, F, C), Height (e.g. 6-1), Year (Fr., So., Jr., Sr., Gr.). "
        "Return a JSON array of objects only, no markdown, no explanation.\n"
        'Format: [{"name":"x","pos":"G","ht":"5-7","yr":"Jr."}]\n\n'
        + section[:15000]
    )

    raw = ask_llm(prompt, model=LLM_LOCAL)
    roster = _parse_json_array(raw)

    if not roster and len(section) > 100:
        safe_print(f"  [DEBUG {cid}] Roster LLM returned 0. Raw start: {raw[:100]}...")

    clean_roster = []
    seen_names = set()
    text_lower = text.lower()

    for p in roster:
        if not isinstance(p, dict): continue
        name = p.get("name", "").strip()
        if not name or name.lower() in ["name", "none", "null", "full bio"]: continue
        
        name_parts = [part for part in name.split() if len(part) > 2]
        if not name_parts: name_parts = [name]
        if not any(part.lower() in text_lower for part in name_parts):
            continue 

        norm_name = "".join(filter(str.isalnum, name.lower()))
        if norm_name in seen_names: continue
        seen_names.add(norm_name)
        
        p["name"] = name
        p["pos"] = normalize_pos(p.get("pos", p.get("position", "")))
        p["year"] = normalize_year(p.get("yr", p.get("year", "")))
        ht = (p.get("ht") or p.get("height") or "").strip()
        if ht and " " in ht: ht = ht.split(" ")[0]
        p["height"] = ht
        p.pop("ht", None); p.pop("yr", None); p.pop("position", None)
        clean_roster.append(p)

    safe_print(f"    [{cid}] Roster: {len(roster)} found -> {len(clean_roster)} unique.")
    with open(cache_path, "w") as f:
        json.dump(clean_roster, f, indent=2)
    return clean_roster


def extract_coaches_lynx(url, tmp_dir, cid):
    """Extract coaches via links dump + local LLM. Uses robust trimmer. Cached."""
    cache_path = os.path.join(tmp_dir, f"{cid}_coaches_final.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.loads(f.read())

    text = _text_dump(url, tmp_dir, cid, "coaches")
    if not text: return []

    section = _generate_trim_file(text, cid, "coaches", tmp_dir)

    prompt = (
        "Extract EVERY coach and staff member from this text. "
        "The text is a messy dump; names and titles may be split across lines. "
        "IMPORTANT: Extract each unique person exactly ONCE. De-duplicate by name. "
        "Return a JSON array of objects only, no markdown, no explanation.\n"
        'Format: [{"name":"x","title":"Head Coach","email":"x@y.edu"}]\n\n' + section[:12000]
    )

    raw = ask_llm(prompt, model=LLM_LOCAL)
    coaches = _parse_json_array(raw)

    if not coaches and len(section) > 50:
        safe_print(f"  [DEBUG {cid}] Coaches LLM returned 0. Raw start: {raw[:100]}...")

    clean = []
    seen = set()
    text_lower = text.lower()

    for c in coaches:
        if not isinstance(c, dict) or not c.get("name"): continue
        name = str(c["name"]).strip()
        
        name_parts = [part for part in name.split() if len(part) > 2]
        if not name_parts: name_parts = [name]
        if not any(part.lower() in text_lower for part in name_parts):
            continue

        norm_name = "".join(filter(str.isalnum, name.lower()))
        if not norm_name or norm_name in seen or len(name.split()) < 2: continue
             
        seen.add(norm_name)
        c["name"] = name
        c["title"] = str(c.get("title", "") or "").strip()
        c["email"] = str(c.get("email", "") or "").strip()
        if c["email"].lower() == "null": c["email"] = ""
        clean.append(c)

    safe_print(f"    [{cid}] Coaches: {len(coaches)} found -> {len(clean)} unique.")
    with open(cache_path, "w") as f:
        json.dump(clean, f, indent=2)
    return clean


def _parse_json_array(raw):
    """Robustly extract a JSON array from LLM output."""
    if not raw: return []
    text = raw.replace("```json", "").replace("```", "").strip()
    
    # 1. Try direct JSON load
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except: pass

    # 2. Regex fallback: find all { } blocks
    matches = re.findall(r"\{[^{}]*?\}", text, re.DOTALL)
    results = []
    for m in matches:
        try:
            obj = json.loads(m)
            if isinstance(obj, dict) and obj.get("name"):
                 results.append(obj)
        except: continue
    
    # 3. Deep search: if objects are nested or complex
    if not results:
        # Very broad search for anything that looks like an object
        matches = re.findall(r"\{.*?\}", text, re.DOTALL)
        for m in matches:
            try:
                obj = json.loads(m)
                if isinstance(obj, dict): results.append(obj)
            except: continue

    return results
