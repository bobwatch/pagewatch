import json

import pytest

from pagewatch.importers import parse_changedetection, parse_distill

CD_EXPORT = json.dumps({
    "uuid-1": {
        "url": "https://example.com/pricing",
        "title": "Pricing page",
        "tag": "saas",
        "css_filter": "css:.price-block",
        "time_between_check": {"days": 1, "hours": 2, "minutes": 30},
    },
    "uuid-2": {
        "url": "https://news.example.com",
        "title": "News",
        "css_filter": "css:#main\nxpath://div[@id='ads']",
        "time_between_check": {"weeks": 1},
    },
    "uuid-3": {
        "url": "https://plain.example.com/page",
        "title": "",
    },
    "uuid-4": "not an object",
    "uuid-5": {"title": "No URL here"},
})


def test_parse_changedetection_full_mapping():
    watches, _ = parse_changedetection(CD_EXPORT)
    first = watches[0]
    assert first["name"] == "Pricing page"
    assert first["url"] == "https://example.com/pricing"
    assert first["selector"] == ".price-block"
    assert first["interval"] == 86400 + 7200 + 1800  # 1d 2h 30m in seconds
    assert first["tags"] == ["saas"]


def test_parse_changedetection_xpath_filter_skipped_with_warning():
    watches, warnings = parse_changedetection(CD_EXPORT)
    second = watches[1]
    assert second["selector"] == "#main"  # css: line kept, xpath: line dropped
    assert second["interval"] == 604800  # 1 week
    assert any("xpath" in w for w in warnings)


def test_parse_changedetection_derives_name_from_url():
    watches, _ = parse_changedetection(CD_EXPORT)
    third = watches[2]
    assert third["name"] == "plain-example-com"
    assert "selector" not in third
    assert "interval" not in third


def test_parse_changedetection_skips_bad_entries_with_warnings():
    watches, warnings = parse_changedetection(CD_EXPORT)
    assert len(watches) == 3
    assert any("not an object" in w for w in warnings)
    assert any("missing URL" in w for w in warnings)


def test_parse_changedetection_duplicate_names_skipped():
    text = json.dumps({
        "a": {"url": "https://x.test/1", "title": "dup"},
        "b": {"url": "https://x.test/2", "title": "dup"},
    })
    watches, warnings = parse_changedetection(text)
    assert len(watches) == 1
    assert any("duplicate name" in w for w in warnings)


def test_parse_changedetection_rejects_bad_input():
    with pytest.raises(ValueError):
        parse_changedetection("{not json")
    with pytest.raises(ValueError):
        parse_changedetection('[{"url": "https://x.test"}]')


def _distill_monitor(**overrides):
    monitor = {
        "name": "GPU Tracker",
        "uri": "https://store.example.com/gpus",
        "config": json.dumps({
            "selections": [{
                "frames": [{
                    "index": 0,
                    "excludes": [],
                    "includes": [{"expr": ".product .name", "type": "css"}],
                }],
                "dynamic": True,
            }],
            "ignoreEmptyText": True,
        }),
        "schedule": json.dumps({"type": "INTERVAL", "params": {"interval": 284}}),
        "tags": ["gpus"],
        "content_type": 2,
    }
    monitor.update(overrides)
    return monitor


def test_parse_distill_full_mapping():
    text = json.dumps({"client": {"local": 1}, "data": [_distill_monitor()]})
    watches, warnings = parse_distill(text)
    assert warnings == []
    assert watches == [{
        "name": "GPU Tracker",
        "url": "https://store.example.com/gpus",
        "selector": ".product .name",
        "interval": 284,
        "tags": ["gpus"],
    }]


def test_parse_distill_xpath_selection_skipped_with_warning():
    monitor = _distill_monitor(config=json.dumps({
        "selections": [{"frames": [{"includes": [{"expr": "//div[@id='price']", "type": "xpath"}]}]}],
    }))
    watches, warnings = parse_distill(json.dumps({"data": [monitor]}))
    assert "selector" not in watches[0]
    assert any("xpath" in w for w in warnings)


def test_parse_distill_derives_name_and_tolerates_missing_fields():
    monitor = _distill_monitor(name="", config=None, schedule=None, tags=None)
    watches, warnings = parse_distill(json.dumps({"data": [monitor]}))
    assert warnings == []
    assert watches == [{"name": "store-example-com", "url": "https://store.example.com/gpus"}]


def test_parse_distill_accepts_watches_key_and_skips_bad_entries():
    text = json.dumps({"watches": [_distill_monitor(), "not an object", {"name": "no url"}]})
    watches, warnings = parse_distill(text)
    assert len(watches) == 1
    assert any("not an object" in w for w in warnings)
    assert any("missing URL" in w for w in warnings)


def test_parse_distill_rejects_bad_input():
    with pytest.raises(ValueError):
        parse_distill("{not json")
    with pytest.raises(ValueError):
        parse_distill('{"nothing": true}')
