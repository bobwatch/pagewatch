from email.utils import parsedate_to_datetime
from xml.dom import minidom

from pagewatch.feed import build_rss

TS1 = "2026-08-10T10:00:00+00:00"
TS2 = "2026-08-10T11:00:00+00:00"
TS3 = "2026-08-10T12:00:00+00:00"


def _entry(ts, content_hash, diff=None):
    entry = {"timestamp": ts, "content_hash": content_hash, "text_length": 10}
    if diff is not None:
        entry["diff"] = diff
    return entry


def _watch(name, entries, url="https://x.test"):
    return {"name": name, "url": url, "history": entries}


def _parse(xml):
    return minidom.parseString(xml)


def test_empty_feed_is_wellformed():
    xml = build_rss([], "pagewatch changes", "http://localhost/", "desc")
    doc = _parse(xml)
    channel = doc.getElementsByTagName("channel")[0]
    assert channel.getElementsByTagName("title")[0].firstChild.data == "pagewatch changes"
    assert doc.getElementsByTagName("item") == []


def test_only_entries_with_diff_become_items():
    entries = [
        _entry(TS1, "h1"),                      # no diff key: baseline check
        _entry(TS2, "h2", diff="-a\n+b"),       # real change
        _entry(TS2, "h2"),                      # re-check, unchanged
    ]
    xml = build_rss([_watch("w", entries)], "t", "http://l/", "d")
    doc = _parse(xml)
    items = doc.getElementsByTagName("item")
    assert len(items) == 1


def test_item_fields():
    xml = build_rss([_watch("w", [_entry(TS2, "h2", diff="-a\n+b")])], "t", "http://l/", "d")
    doc = _parse(xml)
    item = doc.getElementsByTagName("item")[0]
    fields = {n.tagName: n.firstChild.data for n in item.childNodes if n.firstChild}
    assert fields["title"] == "Change detected on w"
    assert fields["link"] == "https://x.test"
    assert fields["guid"] == "w-h2"
    assert fields["description"] == "-a\n+b"
    # RFC 822 date, e.g. "Mon, 10 Aug 2026 11:00:00 +0000"
    assert parsedate_to_datetime(fields["pubDate"]).timestamp() > 0


def test_special_characters_are_escaped():
    diff = "-a <b> & \"c\"\n+x ]]> y"
    xml = build_rss([_watch("w<&>", [_entry(TS2, "h2", diff=diff)])], "t", "http://l/", "d")
    doc = _parse(xml)  # raises if not well-formed
    item = doc.getElementsByTagName("item")[0]
    desc = item.getElementsByTagName("description")[0].firstChild.data
    assert desc == diff
    assert "]]>" not in xml.split("<description>", 1)[1].split("</description>", 1)[0]


def test_guids_are_unique():
    entries = [_entry(TS1, "h1", diff="d1"), _entry(TS2, "h2", diff="d2")]
    xml = build_rss([_watch("w", entries)], "t", "http://l/", "d")
    guids = [n.firstChild.data for n in _parse(xml).getElementsByTagName("guid")]
    assert len(set(guids)) == 2


def test_legacy_entries_without_diff_are_skipped():
    entries = [_entry(TS1, "h1"), _entry(TS2, "h2")]
    xml = build_rss([_watch("w", entries)], "t", "http://l/", "d")
    assert _parse(xml).getElementsByTagName("item") == []


def test_merges_watches_sorted_newest_first_with_limit():
    a = _watch("a", [_entry(TS1, "a1", diff="d"), _entry(TS3, "a3", diff="d")], url="https://a.test")
    b = _watch("b", [_entry(TS2, "b2", diff="d")], url="https://b.test")
    xml = build_rss([a, b], "t", "http://l/", "d", limit=2)
    doc = _parse(xml)
    guids = [n.firstChild.data for n in doc.getElementsByTagName("guid")]
    assert guids == ["a-a3", "b-b2"]
    dates = [parsedate_to_datetime(n.firstChild.data)
             for n in doc.getElementsByTagName("pubDate")]
    assert dates == sorted(dates, reverse=True)


def test_naive_timestamp_is_treated_as_utc():
    xml = build_rss([_watch("w", [_entry("2026-08-10T10:00:00", "h1", diff="d")])], "t", "http://l/", "d")
    pub = _parse(xml).getElementsByTagName("pubDate")[0].firstChild.data
    assert parsedate_to_datetime(pub).utcoffset().total_seconds() == 0
