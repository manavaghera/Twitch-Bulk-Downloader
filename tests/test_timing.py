"""Estimates, the countdown, the encoder fallback, the Wikipedia cache - and the
Clip downloader crash when the Autopilot's result came back."""

import time

import pytest

from clipdl import jobs, locks, media, timing, web, wikiviews


@pytest.fixture
def timings(tmp_path, monkeypatch):
    monkeypatch.setattr(timing, "FILE", tmp_path / "timings.json")
    return tmp_path


def test_estimates_start_from_defaults_and_learn(timings):
    assert timing.per("download") == timing.DEFAULTS["download"]
    timing.record("download", 50.0, 10)          # 5 s a clip: a real run beats the guess
    assert timing.per("download") == 5.0
    timing.record("download", 100.0, 10)         # one slow run moves it only part way
    assert 5.0 < timing.per("download") < 10.0
    for _ in range(12):
        timing.record("download", 50.0, 10)
    assert abs(timing.per("download") - 5.0) < 0.1
    timing.record("download", 0, 0)             # nothing measured: ignored
    assert abs(timing.per("download") - 5.0) < 0.1


def test_download_estimate_adds_up_the_steps(timings):
    plain = timing.download_run(10, "video")
    shorts = timing.download_run(10, "short")
    captioned = timing.download_run(10, "short", captions=True)
    assert plain < shorts < captioned
    assert timing.download_run(10, "video", searches=0) < plain


def test_autopilot_estimate_follows_its_settings(timings):
    few = timing.autopilot_run({"source": "list", "game_list": ["A"], "clips_per_game": 3,
                                "output": "video"})
    many = timing.autopilot_run({"source": "rising", "games": 5, "clips_per_game": 10,
                                 "output": "short", "captions": True})
    assert 0 < few < many


def test_friendly_times():
    assert timing.text(12) == "about 10 s"
    assert timing.text(170) == "about 3 min"
    assert timing.text(4200) == "about 1 h 10 min"
    assert timing.clock(65) == "1:05" and timing.clock(3725) == "1:02:05"


def test_countdown_from_progress_lines():
    job = jobs.Job("download", "x", lambda: None, estimate=100)
    job._write("Downloading 10 clip(s)...", "\n")
    job._phase = None
    job._last_line_at = time.time() - 20          # the step began 20 s ago
    job._write("[5/10] Downloading: a.mp4", "\n")
    step, whole = job.eta()
    assert 15 < step < 25                          # 5 done in 20 s: ~20 s for 5 more
    assert whole >= step
    job._write("[1/4] Short ready: a.mp4", "\n")   # a new step starts counting afresh
    assert job._phase[1] == 4 and job._phase[2] == 1


def test_no_countdown_before_anything_is_counted():
    job = jobs.Job("trends", "x", lambda: None)
    job._write("Reading this week's charts...", "\n")
    assert job.eta() == (None, None)


def test_encoder_fallback_swaps_only_the_video_codec():
    args = ["-i", "in.mp4"] + media.HARDWARE["h264_nvenc"] + ["-c:a", "aac", "out.mp4"]
    assert media.uses_hardware(args)
    software = media.software_instead(args)
    assert software == ["-i", "in.mp4"] + media.SOFTWARE + ["-c:a", "aac", "out.mp4"]
    assert not media.uses_hardware(software)


def test_wikipedia_views_are_read_once_a_day(tmp_path, monkeypatch):
    monkeypatch.setattr(wikiviews, "WIKI_VIEWS_FILE", tmp_path / "views.json")
    asked = []

    from datetime import datetime, timedelta, timezone
    day = (datetime.now(timezone.utc).date() - timedelta(days=1)).strftime("%Y%m%d00")

    def fake_get_json(self, url, params=None):
        asked.append(url)
        if params:                                  # "which article is this, really?"
            titles = params["titles"].split("|")
            pages = [{"title": "Game A (video game)" if t == "Game A" else t}
                     for t in titles if t != "Gone"]
            return {"query": {"redirects": [{"from": "Game A", "to": "Game A (video game)"}],
                              "pages": pages + [{"title": "Gone", "missing": True}]}}
        return {"items": [{"timestamp": day, "views": 100}]}
    monkeypatch.setattr(web.WebClient, "get_json", fake_get_json)
    articles = [("en", "Game A"), ("en", "Gone"), ("de", "Spiel B"), ("fr", "Jeu C")]
    first = web.wiki_views(web.WebClient(), articles)
    assert len([u for u in asked if "api.php" in u]) == 3          # one lookup per Wikipedia
    views_asked = [u for u in asked if "per-article" in u]
    assert len(views_asked) == 3                                    # "Gone" has no article
    assert any("Game_A_%28video_game%29" in u for u in views_asked)  # the redirect is followed
    assert first[("en", "Game A")][7][0] == 100 and ("en", "Gone") not in first
    second = web.wiki_views(web.WebClient(), articles)
    assert len(asked) == 6 and second == first      # the same day: nothing asked again


def test_downloader_page_survives_an_autopilot_result(tmp_data, monkeypatch):
    """The crash from the Automation tab: a finished Autopilot run hands back a
    summary dict, and the Clip downloader tab expected a download's result."""
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(locks, "DOWNLOADS", locks.FileLock("downloads-test"))
    monkeypatch.setenv("CLIPDL_NO_COLLECTOR", "1")
    job = jobs.start("download", "Autopilot run", lambda: {"games": [], "radar": 0})
    while job.running:
        time.sleep(0.05)

    def page():
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path.cwd() / "scripts"))
        from clipdl import jobs as page_jobs, ui_downloader
        ui_downloader.render_result(page_jobs.latest("download").result)
        import streamlit as st
        st.write("ok")
    at = AppTest.from_function(page, default_timeout=60).run()
    assert not at.exception and at.markdown[0].value == "ok"
