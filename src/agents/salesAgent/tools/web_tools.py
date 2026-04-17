"""
Web Tools for Sales Agent
Provides capabilities for deep web research and content extraction.
"""

import requests
from bs4 import BeautifulSoup
from google.adk.tools import FunctionTool as tool
from loguru import logger


@tool
def read_url(url: str) -> str:
    """
    Fetches the full text content of a given URL and returns it as cleaned text.
    Use this when you need deep details from a specific page (e.g., press releases,
    detailed product specs, or financial reports) that aren't available in search snippets.

    Args:
        url: The absolute URL of the page to read.

    Returns:
        The cleaned text content of the page, or an error message if the fetch fails.
    """
    try:
        logger.info(f"Reading URL: {url}")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Remove script and style elements
        for script_or_style in soup(["script", "style"]):
            script_or_style.decompose()

        # Get text
        text = soup.get_text(separator="\n")

        # Break into lines and remove leading and trailing whitespace on each
        lines = (line.strip() for line in text.splitlines())
        # Break multi-headlines into a line each
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        # Drop blank lines
        text = "\n".join(chunk for chunk in chunks if chunk)

        # Limit text size to avoid token overflow in agent memory (approx 10k chars)
        if len(text) > 10000:
            text = text[:10000] + "... [Content Truncated]"

        return text
    except Exception as e:
        logger.error(f"Failed to read URL {url}: {e}")
        return f"Error reading URL: {str(e)}"
