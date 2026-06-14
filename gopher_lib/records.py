"""Record scraping from SIDEARM athletics schedule pages."""
import re
from gopher_lib import html_to_text


def scrape_record(html):
    """Extract overall and conference record from schedule page."""
    text = html_to_text(html)
    # Pattern 1: "Overall X-Y ... Conf X-Y"
    m = re.search(r"Overall\s+(\d{1,2}-\d{1,2}).*?Conf(?:erence)?\s+(\d{1,2}-\d{1,2})", text)
    if m:
        return {"overall": m.group(1), "conference": m.group(2)}
    # Pattern 2: "Overall Wins X Losses Y ... Conf Wins X Losses Y"
    m = re.search(
        r"Overall\s+Wins\s+(\d+)\s+Losses\s+(\d+).*?Conf(?:erence)?\s+Wins\s+(\d+)\s+Losses\s+(\d+)",
        text, re.IGNORECASE,
    )
    if m:
        return {"overall": f"{m.group(1)}-{m.group(2)}", "conference": f"{m.group(3)}-{m.group(4)}"}
    return None
