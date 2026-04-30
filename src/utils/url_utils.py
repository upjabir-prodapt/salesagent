from urllib.parse import urlparse

TRUSTED_DOMAINS = [
    "reuters.com", "bloomberg.com", "ft.com", "wsj.com",
    "bbc.com", "gov.in", "gov.uk", ".gov", "who.int",
    "sec.gov", "europa.eu", "businesswire.com", "prnewswire.com",
]

def is_authoritative(url: str) -> bool:
    if not url:
        return False
    try:
        domain = urlparse(url).netloc.replace("www.", "")
        return any(domain.endswith(d) for d in TRUSTED_DOMAINS)
    except Exception:
        return False
