import json
import os
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from clipdl import alerts, cleanup


@pytest.fixture
def alert_files(tmp_data, monkeypatch):
    monkeypatch.setattr(alerts, "SETTINGS", tmp_data / "alerts.json")
    monkeypatch.setattr(alerts, "SENT", tmp_data / "alerts_sent.json")
    monkeypatch.setattr(alerts, "_sent_lock", alerts.FileLock("alerts-test"))
    return tmp_data


@pytest.fixture
def fake_server():
    """A local stand-in for Discord: records what was posted to it."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, json.loads(self.rfile.read(
                int(self.headers["Content-Length"])))))
            # Discord answers 204 No Content, Telegram 200 OK.
            self.send_response(200 if self.path.endswith("/sendMessage") else 204)
            self.end_headers()

        def log_message(self, *args):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % server.server_port, received
    server.shutdown()


def test_settings_are_validated(alert_files):
    assert alerts.save_settings(discord="https://example.com/hook")
    assert alerts.save_settings(telegram_token="nope")
    assert alerts.save_settings(discord="https://discord.com/api/webhooks/123/abc-DEF_1") is None
    assert alerts.configured()


def test_messages_reach_discord_and_telegram(fake_server):
    base, received = fake_server
    alerts._discord(base + "/api/webhooks/1/x", "hello")
    alerts._telegram("123456:" + "a" * 35, "42", "hi there", base=base)
    assert received[0][1]["content"] == "hello"
    assert received[0][1]["allowed_mentions"] == {"parse": []}      # no @everyone pings
    assert received[1][0].endswith("/sendMessage") and received[1][1]["chat_id"] == "42"


def test_each_alert_goes_out_once(alert_files, monkeypatch):
    alerts.save_settings(discord="https://discord.com/api/webhooks/1/x")
    sent = []
    monkeypatch.setattr(alerts, "send", lambda text, config=None: sent.append(text) or [])
    assert alerts.notify("spike", "spike:game", "first")
    assert not alerts.notify("spike", "spike:game", "again")
    assert alerts.notify("spike", "spike:other", "other game")
    assert sent == ["first", "other game"]


def test_failed_alert_is_tried_again(alert_files, monkeypatch):
    alerts.save_settings(discord="https://discord.com/api/webhooks/1/x")
    monkeypatch.setattr(alerts, "send", lambda text, config=None: ["Discord: down"])
    assert not alerts.notify("live", "live:1", "x")
    monkeypatch.setattr(alerts, "send", lambda text, config=None: [])
    assert alerts.notify("live", "live:1", "x")


def test_switched_off_events_stay_quiet(alert_files, monkeypatch):
    alerts.save_settings(discord="https://discord.com/api/webhooks/1/x",
                         events={"spike": False})
    monkeypatch.setattr(alerts, "send", lambda text, config=None: pytest.fail("sent"))
    assert not alerts.notify("spike", "spike:g", "x")


def test_cleanup_finds_only_old_dated_folders(tmp_path):
    old = (date.today() - timedelta(days=30)).isoformat()
    new = date.today().isoformat()
    for folder in ("Autopilot/" + old, "Autopilot/" + new, "Clip radar/" + old,
                   "VALORANT/Shorts", "Autopilot/notes"):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "Autopilot" / old / "a.mp4").write_bytes(b"x" * 10)
    found = cleanup.old_folders(14, tmp_path)
    assert sorted(str(f["path"].relative_to(tmp_path)).replace(os.sep, "/") for f in found) == \
        ["Autopilot/" + old, "Clip radar/" + old]
    assert next(f for f in found if "Autopilot" in str(f["path"]))["bytes"] == 10


def test_cleanup_refuses_anything_but_dated_folders(tmp_path):
    keep = tmp_path / "VALORANT"
    keep.mkdir()
    moved, _freed, problems = cleanup.to_recycle_bin([{"path": keep, "bytes": 0}])
    assert moved == 0 and problems and keep.exists()
