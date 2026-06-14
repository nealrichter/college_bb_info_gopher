"""School facts extraction — delegates to gopher_lib.llm.ask_about_facts."""
from gopher_lib.llm import ask_about_facts


def extract_school_facts(college_name, wiki_text, academics_html, tmp_dir, cid, **kwargs):
    """Extract school facts via grounded search. Thin wrapper for backward compat."""
    return ask_about_facts(college_name, tmp_dir, cid, **kwargs)
