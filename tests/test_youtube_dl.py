"""The YouTube downloader's own logic - no YouTube needed."""

import subprocess

import pytest

from clipdl import shorts, ytdl, ytlive

FFMPEG = shorts.find_ffmpeg()


@pytest.fixture(autouse=True)
def private_files(tmp_path, monkeypatch):
    monkeypatch.setattr(ytdl, "SETTINGS", tmp_path / "youtube_dl.json")
    monkeypatch.setattr(ytdl, "COOKIE_FILE", tmp_path / "cookies.txt")
    monkeypatch.setattr(ytdl, "HISTORY", tmp_path / "history.json")


def test_formats_never_go_above_the_cap():
    assert ytdl.format_for(2160).startswith("bv*[height<=2160]+ba[ext=m4a]")
    assert ytdl.format_for(1080, live=True).startswith("b[height<=1080]")   # muxed live first
    assert ytdl.format_for(2160, audio_only=True) == "ba/b"


def test_size_at_a_quality():
    item = {"heights": {2160: 1200, 1080: 300, 720: 150}}
    assert ytdl.size_at(item, 2160) == 1200
    assert ytdl.size_at(item, 1440) == 300          # nothing at 1440: the next one down
    assert ytdl.size_at(item, 360) == 0


def test_errors_are_explained():
    assert "not a bot" in ytdl.explain(Exception("ERROR: Sign in to confirm you're not a bot"))
    assert "private" in ytdl.explain(Exception("ERROR: Private video")).lower()
    assert "members" in ytdl.explain(Exception("Join this channel to get access")).lower()
    assert ytdl.explain(Exception("ERROR: something odd\nmore")) == "something odd"


def test_cookies_are_checked_and_used(tmp_path):
    assert ytdl.save_cookie_file(b"not cookies")
    good = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n"
    assert ytdl.save_cookie_file(good) is None
    options = ytdl.options()
    assert options["cookiefile"] == str(ytdl.COOKIE_FILE)
    assert "web_embedded" in options["extractor_args"]["youtube"]["player_client"]
    ytdl.remove_cookies()
    assert "cookiefile" not in ytdl.options() and not ytdl.COOKIE_FILE.exists()
    ytdl.save_settings(cookies="browser:firefox")
    assert ytdl.options()["cookiesfrombrowser"] == ("firefox",)


def test_a_stopped_download_leaves_nothing_behind(tmp_path):
    (tmp_path / "Video [1080p] [abc].f299.mp4.part").write_bytes(b"x")
    (tmp_path / "Video [1080p] [abc].f140.m4a.part").write_bytes(b"x")
    (tmp_path / "Other [def].mp4").write_bytes(b"keep")
    with pytest.raises(ValueError, match="Stopped"):
        ytdl._stopped(tmp_path, "abc")
    assert [p.name for p in tmp_path.iterdir()] == ["Other [def].mp4"]


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed")
def test_live_parts_are_joined_into_one_mp4(tmp_path):
    parts = []
    for n in (1, 2):
        part = tmp_path / ("rec part%d.ts" % n)
        subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=320x240:rate=25:duration=2", "-f", "lavfi", "-i",
                        "sine=duration=2", "-c:v", "libx264", "-c:a", "aac", "-shortest",
                        "-f", "mpegts", str(part)], check=True)
        parts.append(part)
    target = ytlive._finish(parts, tmp_path / "rec 2026-09-24 1613 [720p] [abc].mp4", 0)
    from clipdl import media
    assert abs(media.probe(target)["duration"] - 4) < 0.5
    assert not any(p.exists() for p in parts)
    assert target.name == "rec 2026-09-24 1613 [720p] [abc].mp4"


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed")
def test_recording_stops_cleanly_on_cancel(tmp_path, monkeypatch):
    """A local 60 s HLS stream stands in for YouTube: Cancel after ~3 s must leave
    a short, playable recording - ffmpeg asked to finish, not killed."""
    import threading
    import time
    from clipdl import media
    from clipdl.config import STOP, set_kind
    hls = tmp_path / "hls"
    hls.mkdir()
    subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=25:duration=60", "-f", "lavfi", "-i",
                    "sine=duration=60", "-c:v", "libx264", "-g", "25", "-c:a", "aac",
                    "-shortest", "-f", "hls", "-hls_time", "1", "-hls_list_size", "0",
                    str(hls / "live.m3u8")], check=True)
    part = tmp_path / "rec part1.ts"
    result = {}

    def record():
        set_kind("test-live")
        # -re would pace it like a live stream; reading a local file is instant, so
        # read it slowly the way a real stream arrives.
        monkeypatch.setattr(ytlive, "REPORT_EVERY", 1)
        result["by_hand"] = ytlive._run(FFMPEG, str(hls / "live.m3u8"), {}, part,
                                        time.time(), [part])
    original = subprocess.Popen

    def paced(command, *args, **kwargs):
        return original(command[:1] + ["-re"] + command[1:], *args, **kwargs)
    monkeypatch.setattr(ytlive.subprocess, "Popen", paced)
    thread = threading.Thread(target=record)
    thread.start()
    time.sleep(3)
    STOP.event("test-live").set()
    thread.join(30)
    STOP.event("test-live").clear()
    assert result["by_hand"] is True
    duration = media.probe(part)["duration"]
    assert 1 < duration < 10                         # stopped early, and the file is readable
