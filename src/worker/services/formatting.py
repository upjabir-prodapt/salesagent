"""Report formatting and markdown cleaning helpers."""

from __future__ import annotations

import re


def clean_markdown_report(text: str) -> str:
    """Clean markdown report text by removing code fences, comments, and extra blanks."""
    if not text:
        return ""

    # Remove outer ```markdown ... ``` or ``` ... ```
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    # Remove C-style /* ... */ comments
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)

    # Normalize excessive blank lines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return cleaned.strip()


__all__ = ["clean_markdown_report"]
