import os
import tempfile

from pagewatch.utils import (
    compute_diff,
    content_hash,
    data_dir,
    extract_text,
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
