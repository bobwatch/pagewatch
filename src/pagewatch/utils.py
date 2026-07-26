#!/usr/bin/env python
import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        url = "https://" + url
        parsed = urlparse(url)
    return parsed.geturl()


def is_valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return bool(parsed.scheme and parsed.netloc)


def fetch_page(url: str, timeout: int = 30) -> tuple[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "").lower()
    encoding = "utf-8"
    if "charset=" in content_type:
        encoding = content_type.split("charset=")[-1].split(";")[0].strip()
    resp.encoding = encoding
    return resp.text, url


def extract_text(html: str, selector: str | None = None) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if selector:
        elements = soup.select(selector)
        if not elements:
            return ""
        return "\n".join(el.get_text(strip=True) for el in elements)
    for tag in ("script", "style", "nav", "footer", "header", "noscript"):
        for el in soup.find_all(tag):
            el.decompose()
    return soup.get_text(separator="\n", strip=True)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_diff(old_text: str, new_text: str) -> str:
    import difflib
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = difflib.unified_diff(old_lines, new_lines, n=3, lineterm="")
    return "\n".join(diff)


def data_dir() -> Path:
    """Return the pagewatch data directory.

    Defaults to ~/.pagewatch; override with the PAGEWATCH_HOME environment
    variable (useful for tests, containers, and multi-profile setups).
    """
    override = os.environ.get("PAGEWATCH_HOME")
    d = Path(override).expanduser() if override else Path.home() / ".pagewatch"
    d.mkdir(parents=True, exist_ok=True)
    return d
