"""Tests for src/worker/services/formatting.py."""

from __future__ import annotations

from src.worker.services.formatting import clean_markdown_report


def test_clean_markdown_report_empty_string_returns_empty():
    assert clean_markdown_report("") == ""
    assert clean_markdown_report(None) == ""  # type: ignore[arg-type]


def test_clean_markdown_report_strips_outer_code_fence():
    text = "```markdown\n# Title\nBody\n```"
    assert clean_markdown_report(text) == "# Title\nBody"


def test_clean_markdown_report_strips_plain_fence():
    text = "```\n# Title\n```"
    assert clean_markdown_report(text) == "# Title"


def test_clean_markdown_report_removes_c_style_comments():
    text = "# Title\n/* internal note */\nBody"
    assert "internal note" not in clean_markdown_report(text)


def test_clean_markdown_report_collapses_excess_blank_lines():
    text = "# Title\n\n\n\n\nBody"
    cleaned = clean_markdown_report(text)
    assert "\n\n\n" not in cleaned


def test_clean_markdown_report_passthrough_normal_text():
    text = "# Title\n\nParagraph one.\n\nParagraph two."
    assert clean_markdown_report(text) == text
