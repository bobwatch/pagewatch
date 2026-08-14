import hashlib
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import soupsieve
from bs4 import BeautifulSoup


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        url = "https://" + url
        parsed = urlparse(url)
    return parsed.geturl()


def is_valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


def default_watch_name(url: str) -> str:
    """Derive a filesystem-safe watch name from a URL's host (incl. port)."""
    host = urlparse(url).netloc.lower()
    name = re.sub(r"[^a-z0-9-]+", "-", host)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name or "watch"


def validate_selector(selector: str) -> None:
    """Validate a CSS selector, raising ValueError if the syntax is invalid."""
    try:
        BeautifulSoup("", "html.parser").select(selector)
    except soupsieve.SelectorSyntaxError as exc:
        raise ValueError(f"Invalid CSS selector: {selector!r}") from exc


def _parse_charset(content_type: str) -> str | None:
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            cs = part.split("=", 1)[1].strip().strip("'\"")
            if cs:
                return cs
    return None


def fetch_page(
    url: str,
    timeout: int = 30,
    proxy: str | None = None,
    retries: int = 2,
    backoff: float = 1.5,
    getter=None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    if extra_headers:
        headers.update(extra_headers)
    proxies = {"http": proxy, "https": proxy} if proxy else None
    get = getter or requests.get
    attempts = max(0, int(retries)) + 1
    last_exc: Exception | None = None

    for attempt in range(attempts):
        try:
            resp = get(url, headers=headers, timeout=timeout, proxies=proxies)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").lower()
            charset = _parse_charset(content_type)
            if charset:
                resp.encoding = charset
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


def fetch_page_rendered(
    url: str,
    timeout: int = 30,
    extra_headers: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Fetch a page with a headless Chromium (Playwright), returning the rendered HTML.

    Playwright is an optional dependency (``pip install pagewatch[render]``).
    All browser errors are wrapped in RuntimeError so callers monitoring
    daemons are not killed by unexpected playwright exception types.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for --render. "
            "Install with: pip install pagewatch[render] && playwright install chromium"
        ) from exc

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(extra_http_headers=extra_headers or None)
                try:
                    page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
                except Exception:  # noqa: BLE001 — any playwright failure retries with a plain load
                    # networkidle can time out on pages with long-lived
                    # connections (analytics, websockets) — fall back to load.
                    page.goto(url, timeout=timeout * 1000, wait_until="load")
                return page.content(), url
            finally:
                browser.close()
    except RuntimeError:
        raise
    except Exception as exc:  # playwright errors must not kill the check daemon
        raise RuntimeError(f"Rendered fetch failed for {url}: {exc}") from exc


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
