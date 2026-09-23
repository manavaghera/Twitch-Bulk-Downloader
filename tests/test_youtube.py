"""Sign-in, channel results, uploads and the repost check - against a local
stand-in for Google, so no real account or quota is touched."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from clipdl import mychannel, reposts, uploader, yt_quota, ytauth


@pytest.fixture
def google(tmp_data, monkeypatch):
    """A fake Google: token endpoint, YouTube API and the upload endpoint."""
    for module, name, file in ((ytauth, "CLIENT_FILE", "client.json"),
                               (ytauth, "TOKEN_FILE", "token.json"),
                               (mychannel, "CACHE", "my_channel.json"),
                               (mychannel, "UPLOADS", "uploads.json"),
                               (uploader, "UPLOADS", "uploads.json"),
                               (mychannel, "MANIFEST_FILE", "manifest.json"),
                               (reposts, "CACHE", "reposts.json")):
        monkeypatch.setattr(module, name, tmp_data / file)
    (tmp_data / "manifest.json").write_text(json.dumps({"clips": {"c1": {
        "url": "https://clips.twitch.tv/FunnySlug", "game": "VALORANT", "streamer": "Tarik",
        "status": "downloaded"}}}), encoding="utf-8")
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, body, status=200, headers=None):
            data = json.dumps(body).encode()
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            size = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(size).decode()
            seen.append(("POST", self.path, body, dict(self.headers)))
            if self.path.startswith("/token"):
                form = parse_qs(body)
                if form.get("grant_type") == ["authorization_code"]:
                    assert form["code"] == ["good-code"] and form["code_verifier"][0]
                    return self._reply({"access_token": "A1", "refresh_token": "R1",
                                        "expires_in": 3600})
                return self._reply({"access_token": "A2", "expires_in": 3600})
            if self.path.startswith("/upload"):
                return self._reply({}, headers={"Location": "http://127.0.0.1:%d/put/1"
                                                % self.server.server_port})

        def do_PUT(self):
            size = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(size)
            seen.append(("PUT", self.path, size, dict(self.headers)))
            self._reply({"id": "vid123"})

        def do_GET(self):
            seen.append(("GET", self.path, "", dict(self.headers)))
            path = urlparse(self.path).path
            if path.endswith("/channels"):
                return self._reply({"items": [{"id": "UC1", "snippet": {"title": "Me"},
                                               "statistics": {"subscriberCount": "12"},
                                               "contentDetails": {"relatedPlaylists": {
                                                   "uploads": "UU1"}}}]})
            if path.endswith("/playlistItems"):
                return self._reply({"items": [{"contentDetails": {"videoId": v}}
                                              for v in ("v1", "v2", "v3")]})
            if path.endswith("/videos"):
                return self._reply({"items": [
                    {"id": "v1", "snippet": {"title": "Tarik ACE", "description":
                                             "Original clip: https://clips.twitch.tv/FunnySlug",
                                             "publishedAt": "2026-09-01T00:00:00Z"},
                     "statistics": {"viewCount": "1000"}, "contentDetails": {"duration": "PT31S"}},
                    {"id": "v2", "snippet": {"title": "tarik again", "description": "",
                                             "publishedAt": "2026-09-02T00:00:00Z"},
                     "statistics": {"viewCount": "3000"}, "contentDetails": {"duration": "PT50S"}},
                    {"id": "v3", "snippet": {"title": "a long video", "description": "",
                                             "publishedAt": "2026-09-02T00:00:00Z"},
                     "statistics": {"viewCount": "9"}, "contentDetails": {"duration": "PT20M"}}]})
            if path.endswith("/search"):
                return self._reply({"items": [
                    {"id": {"videoId": "s1"}, "snippet": {"title": "INSANE ACE by Tarik",
                                                         "channelTitle": "Reposter"}},
                    {"id": {"videoId": "s2"}, "snippet": {"title": "cooking pasta",
                                                         "channelTitle": "Chef"}}]})
            self._reply({}, 404)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_port
    yield base, seen
    server.shutdown()


def test_client_file_is_checked(google):
    assert ytauth.save_client("not json")
    assert "Desktop app" in ytauth.save_client(json.dumps({"web": {"client_id": "a",
                                                                   "client_secret": "b"}}))
    assert ytauth.save_client(json.dumps({"installed": {"client_id": "a",
                                                        "client_secret": "b"}})) is None


def test_sign_in_through_the_browser_redirect(google):
    base, seen = google
    ytauth.save_client(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}))
    url = ytauth.start_connect(open_browser=False, auth_url=base + "/auth",
                               token_url=base + "/token")
    query = parse_qs(urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"] and query["access_type"] == ["offline"]
    redirect, state = query["redirect_uri"][0], query["state"][0]
    # A forged redirect (wrong state) is refused...
    assert requests.get(redirect, params={"code": "bad", "state": "x"}).status_code == 400
    # ...the real one - what Google sends the browser back with - connects.
    assert requests.get(redirect, params={"code": "good-code", "state": state}).ok
    for _ in range(50):
        if ytauth.status()["status"] != "waiting":
            break
        time.sleep(0.1)
    assert ytauth.status()["status"] == "connected" and ytauth.connected()
    assert ytauth.access_token() == "A1"


def connect(base):
    ytauth.save_client(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}))
    ytauth.save_json(ytauth.TOKEN_FILE, {"refresh_token": "R1", "access_token": "A1",
                                         "expires_at": time.time() + 3000})


def test_expired_token_is_refreshed(google):
    base, _seen = google
    connect(base)
    token = json.loads(ytauth.TOKEN_FILE.read_text())
    token["expires_at"] = 0
    ytauth.save_json(ytauth.TOKEN_FILE, token)
    assert ytauth.access_token(token_url=base + "/token") == "A2"


def test_channel_results_by_game_and_streamer(google, monkeypatch):
    base, seen = google
    connect(base)
    monkeypatch.setattr(mychannel, "API", base + "/youtube/v3/")
    data = mychannel.fetch()
    assert data["channel"]["title"] == "Me" and len(data["videos"]) == 3
    by_game = mychannel.results(data, "game")
    assert [(r["name"], r["videos"]) for r in by_game] == [("VALORANT", 1)]   # v1: clip link
    by_streamer = mychannel.results(data, "streamer")
    assert by_streamer[0]["name"] == "Tarik" and by_streamer[0]["videos"] == 2  # v2: by name
    assert all(h.get("Authorization") == "Bearer A1" for m, _p, _b, h in seen if m == "GET")
    assert yt_quota.used_today() == 3                   # one unit per call
    assert mychannel.best_games(3, min_videos=1) == ["VALORANT"]


def test_upload_is_scheduled_and_recorded(google, tmp_data):
    base, seen = google
    connect(base)
    video = tmp_data / "short.mp4"
    video.write_bytes(b"x" * 1234)
    from datetime import datetime, timedelta, timezone
    when = datetime.now(timezone.utc) + timedelta(days=1)
    video_id = uploader.upload(video, "My <title>", "desc", ["#valorant", "shorts"],
                               "public", when, {"clip_id": "c1", "game": "VALORANT"},
                               upload_url=base + "/upload")
    assert video_id == "vid123"
    start = next(s for s in seen if s[0] == "POST" and s[1].startswith("/upload"))
    body = json.loads(start[2])
    assert body["snippet"]["title"] == "My title" and body["snippet"]["tags"] == ["valorant",
                                                                                   "shorts"]
    assert body["status"]["privacyStatus"] == "private" and body["status"]["publishAt"]
    assert next(s for s in seen if s[0] == "PUT")[2] == 1234
    assert uploader.uploaded_clips() == {"c1"}
    assert yt_quota.used_today() == uploader.COST


def test_no_upload_without_quota(google, tmp_data):
    base, _seen = google
    connect(base)
    yt_quota.spend(9000, "search")
    video = tmp_data / "short.mp4"
    video.write_bytes(b"x")
    with pytest.raises(ValueError, match="units"):
        uploader.upload(video, "t", "d", upload_url=base + "/upload")


def test_sidecar_is_read_back(tmp_path):
    from clipdl import titles
    video = tmp_path / "a.mp4"
    titles.write_sidecar(video, titles.suggest("nice ace", "Tarik", "VALORANT", "valorant",
                                               "https://clips.twitch.tv/X"))
    notes = uploader.read_sidecar(video)
    assert len(notes["titles"]) == 3 and notes["titles"][0].startswith("Tarik")
    assert "clips.twitch.tv/X" in notes["description"] and "#valorant" in notes["hashtags"]


def test_repost_check_finds_the_same_moment(google, monkeypatch):
    base, _seen = google
    from clipdl.web import WebClient
    real_get = WebClient.get_json
    monkeypatch.setattr(WebClient, "get_json", lambda self, url, params=None: real_get(
        self, url.replace("https://www.googleapis.com", base), params))
    clip = {"id": "c9", "title": "INSANE ACE", "broadcaster_name": "Tarik"}
    result = reposts.check("key", clip)
    assert [m["channel"] for m in result["matches"]] == ["Reposter"]
    assert reposts.cached("c9") is not None
