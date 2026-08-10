import tempfile
from contextlib import contextmanager
from pathlib import Path

from pagewatch.alerts import AlertManager, build_payload
from pagewatch.cli import _mask_url
from pagewatch.storage import Storage


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class FakeSession:
    def __init__(self, status_code=200, exc=None):
        self.status_code = status_code
        self.exc = exc
        self.posts = []

    def post(self, url, json=None, data=None, timeout=None):
        self.posts.append({"url": url, "json": json, "data": data, "timeout": timeout})
        if self.exc is not None:
            raise self.exc
        return FakeResponse(self.status_code)


@contextmanager
def manager(status_code=200, exc=None):
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp))
        session = FakeSession(status_code=status_code, exc=exc)
        yield AlertManager(storage=store, session=session), session, store


def changed_result(name="w", diff="-old\n+new\n"):
    return {
        "name": name,
        "url": f"https://{name}.test",
        "timestamp": "2026-07-26T00:00:00+00:00",
        "changed": True,
        "error": None,
        "diff": diff,
        "current_hash": "abc123",
    }


def error_result(name="w"):
    return {
        "name": name,
        "url": f"https://{name}.test",
        "timestamp": "2026-07-26T00:00:00+00:00",
        "changed": False,
        "error": "HTTP 500",
        "diff": None,
    }


def unchanged_result(name="w"):
    return {
        "name": name,
        "url": f"https://{name}.test",
        "timestamp": "2026-07-26T00:00:00+00:00",
        "changed": False,
        "error": None,
        "diff": None,
    }


def test_add_channel_validation_and_autonaming():
    with manager() as (am, _session, _store):
        c1 = am.add_channel("https://hooks.example/a")
        c2 = am.add_channel("https://hooks.example/b")
        assert c1["name"] == "webhook-1"
        assert c2["name"] == "webhook-2"

        try:
            am.add_channel("https://hooks.example/c", fmt="nope")
            raise AssertionError("expected ValueError for bad format")
        except ValueError:
            pass

        try:
            am.add_channel("https://hooks.example/c", events="sometimes")
            raise AssertionError("expected ValueError for bad events")
        except ValueError:
            pass

        try:
            am.add_channel("ftp://hooks.example/c")
            raise AssertionError("expected ValueError for bad url")
        except ValueError:
            pass

        try:
            am.add_channel("https://hooks.example/c", name="webhook-1")
            raise AssertionError("expected ValueError for duplicate name")
        except ValueError:
            pass


def test_channels_persist_in_config():
    with manager() as (am, _session, store):
        am.add_channel("https://hooks.example/a", name="ops", fmt="slack", events="all")
        fresh = AlertManager(storage=store, session=FakeSession())
        channels = fresh.list_channels()
        assert len(channels) == 1
        assert channels[0] == {"name": "ops", "url": "https://hooks.example/a", "format": "slack", "events": "all"}


def test_remove_channel():
    with manager() as (am, _session, _store):
        am.add_channel("https://hooks.example/a", name="ops")
        assert am.remove_channel("ops") is True
        assert am.list_channels() == []
        assert am.remove_channel("ops") is False


def test_payload_formats():
    event = {"event": "change", "name": "w", "url": "https://w.test", "diff_preview": "-a\n+b"}
    slack = build_payload("slack", event)
    assert set(slack) == {"text"}
    assert "change detected" in slack["text"]

    discord = build_payload("discord", event)
    assert set(discord) == {"content"}

    feishu = build_payload("feishu", event)
    assert feishu["msg_type"] == "text"
    assert "change detected" in feishu["content"]["text"]

    dingtalk = build_payload("dingtalk", event)
    assert dingtalk["msgtype"] == "text"
    assert "change detected" in dingtalk["text"]["content"]

    generic = build_payload("generic", event)
    assert generic["source"] == "pagewatch"
    assert generic["event"] == "change"
    assert generic["name"] == "w"
    assert generic["diff_preview"] == "-a\n+b"
    assert "sent_at" in generic


def test_dispatch_routes_by_event_kind():
    with manager() as (am, session, _store):
        am.add_channel("https://hooks.example/change", name="on-change", events="change")
        am.add_channel("https://hooks.example/error", name="on-error", events="error")
        am.add_channel("https://hooks.example/all", name="on-all", events="all")

        reports = am.dispatch([changed_result(), error_result(), unchanged_result()])

        urls = [p["url"] for p in session.posts]
        assert urls.count("https://hooks.example/change") == 1
        assert urls.count("https://hooks.example/error") == 1
        assert urls.count("https://hooks.example/all") == 2
        # 4 webhooks + 2 email reports (email not configured, so 2 "not configured" reports)
        assert len(reports) == 6
        webhook_reports = [r for r in reports if r.get("channel") and r["channel"] != "email"]
        assert len(webhook_reports) == 4
        assert all(r["ok"] for r in webhook_reports)
        email_reports = [r for r in reports if r.get("channel") == "email"]
        assert len(email_reports) == 2
        assert not email_reports[0]["ok"]
        assert not email_reports[1]["ok"]


def test_dispatch_truncates_diff_preview():
    with manager() as (am, session, _store):
        am.add_channel("https://hooks.example/a", name="ops", fmt="generic", events="change")
        am.dispatch([changed_result(diff="x" * 5000)])
        payload = session.posts[0]["json"]
        assert len(payload["diff_preview"]) == 800


def test_dispatch_reports_http_failure():
    with manager(status_code=500) as (am, _session, _store):
        am.add_channel("https://hooks.example/a", name="ops")
        reports = am.dispatch([changed_result()])
        assert reports[0]["ok"] is False
        assert reports[0]["error"] == "HTTP 500"
        assert not reports[1]["ok"]  # email not configured


def test_dispatch_reports_network_exception():
    with manager(exc=ConnectionError("connection refused")) as (am, _session, _store):
        am.add_channel("https://hooks.example/a", name="ops")
        reports = am.dispatch([changed_result()])
        assert reports[0]["ok"] is False
        assert "connection refused" in reports[0]["error"]
        assert not reports[1]["ok"]  # email not configured


def test_send_test_targets_named_or_all_channels():
    with manager() as (am, session, _store):
        am.add_channel("https://hooks.example/a", name="a")
        am.add_channel("https://hooks.example/b", name="b")

        reports = am.send_test()
        assert len(reports) == 3  # 2 webhooks + 1 email report (email not configured)

        session.posts.clear()
        reports = am.send_test("b")
        assert len(reports) == 1
        assert session.posts[0]["url"] == "https://hooks.example/b"

        try:
            am.send_test("nope")
            raise AssertionError("expected ValueError for unknown channel")
        except ValueError:
            pass


def test_email_config_get_set():
    with manager() as (am, _session, store):
        assert am.get_email_config() == {}

        cfg = am.set_email_config(
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            smtp_user="user@gmail.com",
            smtp_pass="app-password",
            from_addr="pagewatch@gmail.com",
            to_addrs="alerts@example.com",
        )
        assert cfg["smtp_host"] == "smtp.gmail.com"
        assert cfg["smtp_port"] == 587
        assert cfg["from_addr"] == "pagewatch@gmail.com"
        assert cfg["to_addrs"] == "alerts@example.com"

        reloaded = am.get_email_config()
        assert reloaded["smtp_host"] == "smtp.gmail.com"
        # password is returned decoded
        assert reloaded.get("smtp_pass") == "app-password"
        # stored obfuscated on disk
        raw_config = store.load_config()
        email_on_disk = raw_config["alerts"]["email"]
        assert "smtp_pass_obfuscated" in email_on_disk
        assert email_on_disk["smtp_pass_obfuscated"] != "app-password"


def test_email_config_empty_host_raises():
    with manager() as (am, _session, _store):
        try:
            am.set_email_config(smtp_host="")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


def test_dispatch_email_event_not_configured():
    with manager() as (am, _session, _store):
        result = am.dispatch_email_event({"event": "change", "name": "t1", "url": "https://x.test"})
        assert result["ok"] is False
        assert "not configured" in result["error"].lower()


def test_update_channel():
    with manager() as (am, _session, _store):
        am.add_channel("https://hooks.example/a", name="ops", fmt="slack", events="change")
        updated = am.update_channel("ops", url="https://hooks.example/b", fmt="discord", events="all")
        assert updated is not None
        assert updated["url"] == "https://hooks.example/b"
        assert updated["format"] == "discord"
        assert updated["events"] == "all"
        assert am.update_channel("nope", url="https://x.test") is None


def test_update_channel_partial():
    with manager() as (am, _session, _store):
        am.add_channel("https://hooks.example/a", name="ops", fmt="slack", events="change")
        am.update_channel("ops", events="all")
        c = am.list_channels()[0]
        assert c["url"] == "https://hooks.example/a"
        assert c["format"] == "slack"
        assert c["events"] == "all"


def test_new_channel_payload_formats():
    event = {"event": "change", "name": "w", "url": "https://w.test", "diff_preview": "-a\n+b"}

    telegram = build_payload("telegram", event, url="https://api.telegram.org/bot123:abc/sendMessage?chat_id=42")
    assert telegram["chat_id"] == "42"
    assert "change detected" in telegram["text"]

    wecom = build_payload("wecom", event)
    assert wecom["msgtype"] == "text"
    assert "change detected" in wecom["text"]["content"]

    gotify = build_payload("gotify", event)
    assert gotify["title"] == "PageWatch"
    assert gotify["priority"] == 5
    assert "change detected" in gotify["message"]

    ntfy = build_payload("ntfy", event)
    assert "change detected" in ntfy["text"]


def test_telegram_missing_chat_id_reports_error():
    try:
        build_payload("telegram", {"event": "test"}, url="https://api.telegram.org/bot123:abc/sendMessage")
        raise AssertionError("expected ValueError for missing chat_id")
    except ValueError as exc:
        assert "chat_id" in str(exc)

    with manager() as (am, session, _store):
        am.add_channel("https://api.telegram.org/bot123:abc/sendMessage", name="tg", fmt="telegram")
        reports = am.send_test("tg")
        assert reports[0]["ok"] is False
        assert "chat_id" in reports[0]["error"]
        assert session.posts == []  # no request attempted


def test_telegram_dispatch_sends_chat_id_and_masks_token():
    url = "https://api.telegram.org/bot123:secret/sendMessage?chat_id=42"
    with manager() as (am, session, _store):
        am.add_channel(url, name="tg", fmt="telegram")
        reports = am.dispatch([changed_result()])
        assert reports[0]["ok"] is True
        assert session.posts[0]["json"]["chat_id"] == "42"
        assert "change detected" in session.posts[0]["json"]["text"]

    masked = _mask_url(url)
    assert "123" not in masked
    assert "secret" not in masked
    assert "42" not in masked


def test_ntfy_posts_plain_text_body():
    with manager() as (am, session, _store):
        am.add_channel("https://ntfy.sh/mytopic", name="ntfy", fmt="ntfy")
        reports = am.dispatch([changed_result()])
        assert reports[0]["ok"] is True
        post = session.posts[0]
        assert post["json"] is None
        assert "change detected" in post["data"]
