import os
import tempfile

import requests

from pagewatch.utils import (
    apply_ignore_patterns,
    compute_diff,
    content_hash,
    data_dir,
    extract_text,
    fetch_page,
    is_valid_url,
    normalize_url,
)

HTML = (
    "<html><head><title>T</title><script>var x=1;</script><style>.a{}</style></head>"
    "<body><nav>Navigation</nav><h1>Hello</h1>"
    '<p class="item">World</p><p class="item">Again</p>'
    "<footer>Footer stuff</footer></body></html>"
)


def test_normalize_url_adds_https():
    assert normalize_url("example.com") == "https://example.com"


def test_normalize_url_keeps_existing_scheme():
    assert normalize_url("http://example.com/a?b=1") == "http://example.com/a?b=1"


def test_normalize_url_strips_whitespace():
    assert normalize_url("  https://example.com  ") == "https://example.com"


def test_is_valid_url():
    assert is_valid_url("https://example.com")
    assert not is_valid_url("not-a-url")
    assert not is_valid_url("")


def test_content_hash_is_deterministic_sha256():
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")
    assert len(content_hash("abc")) == 64


def test_extract_text_strips_page_chrome():
    text = extract_text(HTML)
    assert "Hello" in text
    assert "World" in text
    assert "Navigation" not in text
    assert "var x=1" not in text
    assert "Footer stuff" not in text


def test_extract_text_with_selector():
    assert extract_text(HTML, ".item") == "World\nAgain"


def test_extract_text_selector_without_match_returns_empty():
    assert extract_text(HTML, ".does-not-exist") == ""


def test_apply_ignore_patterns_filters_matching_lines():
    text = "Price: 100\nUpdated at 2026-07-26 11:00:00\nViews: 4123"
    filtered = apply_ignore_patterns(text, [r"Updated at \d{4}", r"^Views:"])
    assert filtered == "Price: 100"


def test_apply_ignore_patterns_noop_without_patterns():
    text = "a\nb"
    assert apply_ignore_patterns(text, None) == text
    assert apply_ignore_patterns(text, []) == text


def test_apply_ignore_patterns_skips_invalid_regex():
    text = "a\nbb"
    assert apply_ignore_patterns(text, ["[invalid"]) == text
    # Valid pattern still applies alongside an invalid one.
    assert apply_ignore_patterns(text, ["[invalid", "^b+$"]) == "a"


def test_compute_diff_marks_changes():
    diff = compute_diff("a\nb\n", "a\nc\n")
    assert "-b" in diff
    assert "+c" in diff


def test_compute_diff_identical_is_empty():
    assert compute_diff("same\n", "same\n") == ""


def test_data_dir_honors_pagewatch_home():
    old = os.environ.get("PAGEWATCH_HOME")
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "pw-home")
        os.environ["PAGEWATCH_HOME"] = target
        try:
            d = data_dir()
            assert str(d) == target
            assert d.is_dir()
        finally:
            if old is None:
                del os.environ["PAGEWATCH_HOME"]
            else:
                os.environ["PAGEWATCH_HOME"] = old


class FakeResponse:
    def __init__(self, status_code=200, text="<html>ok</html>"):
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.encoding = None

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error


class FlakyGetter:
    """Fails ``failures`` times (exception or status), then succeeds."""

    def __init__(self, failures=0, exc=None, fail_status=None):
        self.failures = failures
        self.exc = exc
        self.fail_status = fail_status
        self.calls = 0

    def __call__(self, url, headers=None, timeout=None, proxies=None):
        self.calls += 1
        self.last_proxies = proxies
        if self.calls <= self.failures:
            if self.exc is not None:
                raise self.exc
            return FakeResponse(status_code=self.fail_status)
        return FakeResponse()


def test_fetch_page_retries_connection_errors():
    getter = FlakyGetter(failures=2, exc=requests.ConnectionError("refused"))
    text, _url = fetch_page("https://x.test", retries=2, backoff=0, getter=getter)
    assert text == "<html>ok</html>"
    assert getter.calls == 3


def test_fetch_page_retries_5xx_then_raises_when_exhausted():
    getter = FlakyGetter(failures=5, fail_status=503)
    try:
        fetch_page("https://x.test", retries=1, backoff=0, getter=getter)
        raise AssertionError("expected HTTPError")
    except requests.HTTPError:
        pass
    assert getter.calls == 2  # initial + 1 retry


def test_fetch_page_does_not_retry_4xx():
    getter = FlakyGetter(failures=5, fail_status=404)
    try:
        fetch_page("https://x.test", retries=3, backoff=0, getter=getter)
        raise AssertionError("expected HTTPError")
    except requests.HTTPError:
        pass
    assert getter.calls == 1


def test_fetch_page_passes_proxy():
    getter = FlakyGetter()
    fetch_page("https://x.test", proxy="http://127.0.0.1:8888", retries=0, backoff=0, getter=getter)
    assert getter.last_proxies == {"http": "http://127.0.0.1:8888", "https": "http://127.0.0.1:8888"}
