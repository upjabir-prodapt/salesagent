"""Formatting utilities for report generation."""

import re


def clean_markdown_report(report: str) -> str:
    """
    Cleans up markdown report formatting:
    1. Fixes double-encoding issues (e.g. Â£ -> £).
    2. Pulls specific tables out of bullet lists to fix PDF rendering.
    3. Extracts all URLs globally, strips them from the text entirely,
       and appends a clean Source Summary bulleted list at the bottom.
    """
    # 1. Encoding Fixes
    replacements = {
        "Â£": "£",
        "â€”": "—",
        "â€™": "'",
        "â€œ": '"',
        "â€": '"',
    }
    for old, new in replacements.items():
        report = report.replace(old, new)

    # 2. Extract Tables from Bullet Lists
    table_titles = ["Use Case Recommendations", "Operational Location Breakdown"]
    for title in table_titles:
        pattern = re.compile(
            rf"^[ \t]*-\s*(\*?.*?(?:{title})\s*Table.*?\*?:?)\s*$", re.MULTILINE
        )
        report = pattern.sub(r"\n\1\n", report)

    # 3. URL Extraction and Clean Source Summary

    # Step A: Global Extraction
    url_pattern = re.compile(r"https?://[^\s\)<>\]]+")
    all_urls = url_pattern.findall(report)

    # Deduplicate while preserving order
    urls = []
    seen = set()
    for url in all_urls:
        clean_url = url.rstrip(".,;:!?")
        if clean_url not in seen:
            seen.add(clean_url)
            urls.append(clean_url)

    # Step B: Truncate
    parts = re.split(
        r"\n##\s*(?:13\.?\s*)?Source\s+Summary", report, maxsplit=1, flags=re.IGNORECASE
    )
    main_content = parts[0]

    # Step C: Strip Inline URLs
    # 1. Markdown Links: Replace [text](url) with text
    main_content = re.sub(r"\[([^\]]+)\]\((?:https?://[^\s\)]+)\)", r"\1", main_content)

    # 2. Source Tags: Remove entirely
    main_content = re.sub(
        r'(?:\[|\()?\s*(?:Source|Sources):\s*https?://[^\s<>)"\]]+[\]\)]?',
        "",
        main_content,
        flags=re.IGNORECASE,
    )

    # 3. Bare URLs: Remove any remaining bare URLs
    main_content = re.sub(r'https?://[^\s<>)"\]]+', "", main_content)

    # Step D: Cleanup empty brackets/parentheses left behind
    main_content = re.sub(r"\(\s*\)", "", main_content)
    main_content = re.sub(r"\[\s*\]", "", main_content)

    # Step E: Rebuild
    summary_lines = ["\n\n## 13. Source Summary\n"]
    if urls:
        for url in urls:
            summary_lines.append(f"- {url}")
    else:
        summary_lines.append("- No external sources cited in this report.")

    return main_content.strip() + "\n" + "\n".join(summary_lines) + "\n"
