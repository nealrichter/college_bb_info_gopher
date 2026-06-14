#!/usr/bin/env python3.11
"""Export all summary Markdown files to a single HTML file for Google Docs import."""
import argparse
import glob
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SUMMARY_DIR = os.path.join(SCRIPT_DIR, "college_summary")


def md_to_html(md):
    """Simple Markdown to HTML conversion (no dependencies)."""
    lines = md.split("\n")
    html_lines = []
    in_table = False
    in_list = False

    for line in lines:
        # Headers
        if line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):
            html_lines.append(f"<h3>{line[4:]}</h3>")
        # Table
        elif line.startswith("|"):
            if "|---" in line:
                continue
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if not in_table:
                html_lines.append("<table border='1' cellpadding='4' cellspacing='0'>")
                in_table = True
            html_lines.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        elif in_table:
            html_lines.append("</table>")
            in_table = False
            html_lines.append(f"<p>{line}</p>" if line.strip() else "")
        # List items
        elif line.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            content = line[2:]
            html_lines.append(f"<li>{content}</li>")
        elif line.startswith("  - "):
            content = line[4:]
            html_lines.append(f"<li style='margin-left:20px'>{content}</li>")
        elif in_list and not line.strip():
            html_lines.append("</ul>")
            in_list = False
        # Links
        elif line.startswith("["):
            m = re.match(r"\[(.+?)\]\((.+?)\)", line)
            if m:
                html_lines.append(f"<p><a href='{m.group(2)}'>{m.group(1)}</a></p>")
            else:
                html_lines.append(f"<p>{line}</p>")
        # Horizontal rule
        elif line.startswith("---"):
            html_lines.append("<hr>")
        # Bold
        elif line.strip():
            line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            line = re.sub(r"\[(.+?)\]\((.+?)\)", r"<a href='\2'>\1</a>", line)
            html_lines.append(f"<p>{line}</p>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False

    if in_table:
        html_lines.append("</table>")
    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def main():
    parser = argparse.ArgumentParser(description="Export summaries to single HTML for Google Docs")
    parser.add_argument("-o", "--output", default=os.path.join(SCRIPT_DIR, "all_colleges_summary.html"))
    args = parser.parse_args()

    files = sorted(glob.glob(os.path.join(SUMMARY_DIR, "*.md")))
    if not files:
        print("No summary files found in college_summary/")
        sys.exit(1)

    html_parts = [
        "<html><head><meta charset='utf-8'><title>College WBB Summaries</title></head><body>",
        "<h1>College WBB Program Summaries</h1>",
    ]

    for f in files:
        with open(f) as fh:
            md = fh.read()
        # Strip Data Provenance section
        md = re.split(r"\n---\n## Data Provenance", md)[0]
        html_parts.append(md_to_html(md))
        html_parts.append("<div style='page-break-after: always'></div>")

    html_parts.append("</body></html>")

    with open(args.output, "w") as f:
        f.write("\n".join(html_parts))

    print(f"Wrote {args.output}: {len(files)} schools")
    print("Import to Google Docs: File → Open → Upload this HTML file")


if __name__ == "__main__":
    main()
