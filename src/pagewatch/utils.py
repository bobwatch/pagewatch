#!/usr/bin/env python
import hashlib
import os
import re
import time
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


def fetch_page(
    url: str,
    timeout: int = 30,
    proxy: str | None = None,
    retries: int = 2,
    backoff: float = 1.5,
    getter=None,
) -> tuple[str, str]:
    """Fetch a page, with optional HTTP(S) proxy and retries.

    Connection errors, timeouts, and 5xx responses are retried with
    exponential backoff (``backoff * 2**attempt`` seconds); 4xx responses
    raise immediately. ``getter`` allows injecting a ``requests.get``
    replacement for testing.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None
    get = getter or requests.get
    attempts = max(0, int(retries)) + 1
    last_exc: Exception | None = None

    for attempt in range(attempts):
        try:
            resp = get(url, headers=headers, timeout=timeout, proxies=proxies)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").lower()
            encoding = "utf-8"
            if "charset=" in content_type:
                encoding = content_type.split("charset=")[-1].split(";")[0].strip()
            resp.encoding = encoding
            return resp.text, url
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status is None or not 500 <= status < 600:
                raise
            last_exc = exc
        except requests.RequestException as exc:
            last_exc = exc
        if attempt < attempts - 1 and backoff:
            time.sleep(backoff * (2 ** attempt))

    raise last_exc


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


def apply_ignore_patterns(text: str, patterns: list[str] | None) -> str:
    """Drop lines matching any of the given regex patterns.

    Used to silence dynamic noise (timestamps, view counters, ads) that would
    otherwise trigger false change alerts. Invalid patterns are skipped at
    runtime; validate with ``re.compile`` when accepting user input.
    """
    if not patterns:
        return text
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            continue
    if not compiled:
        return text
    kept = [line for line in text.splitlines() if not any(c.search(line) for c in compiled)]
    return "\n".join(kept)


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
