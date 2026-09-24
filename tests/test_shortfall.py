"""Search first, then ask: a run that comes up short downloads nothing until the
user has said how to fill the gap."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipdl import filter as clip_filter
from clipdl import session, shortfall
from clipdl.session import DownloadRequest

NOW = datetime.now(timezone.utc)


def clip(n, hours_ago, views, seconds=30, lang="en", title="INSANE ACE clutch"):
    return {"id": "c%d" % n, "title": "%s %d" % (title, n), "broadcaster_id": lang + str(n),
            "broadcaster_name": "Streamer%d" % n, "view_count": views, "duration": seconds,
            "created_at": (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "video_id": "", "url": "https://clips.twitch.tv/c%d" % n}


RECENT = [clip(1, 2, 900), clip(2, 5, 800), clip(3, 7, 700),
          clip(4, 3, 650, seconds=5),                   # too short
          clip(5, 4, 600, lang="de"),                   # another language
          clip(6, 6, 500, title="just chatting with chat")]   # talking
OLDER = [clip(10 + i, 30 + i, 400 - i) for i in range(5)]


class FakeTwitch:
    """Clips for the last 24 hours, and older ones for the days before."""

    def get_page(self, path, params):
        started = datetime.strptime(params["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        recent_window = NOW - started < timedelta(hours=25)
        return (RECENT if recent_window else OLDER), None

    def channel_languages(self, ids):
        return {i: i[:2] for i in ids}

    def video_titles(self, ids):
        return {}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(shortfall, "MANIFEST_FILE", tmp_path / "manifest.json")
    monkeypatch.setattr(shortfall, "LANG_FILE", tmp_path / "languages.json")
    monkeypatch.setattr(clip_filter, "VOD_FILE", tmp_path / "vods.json")
    monkeypatch.setattr(shortfall.permissions, "FILE", tmp_path / "permissions.json")
    return tmp_path


def request(tmp_path, wanted=10, hours=24):
    return DownloadRequest({"id": "1", "name": "VALORANT"}, wanted, ("Last 24 hours", hours),
                           ("Most views", "views"), 10, 60, True, "new", "video",
                           save_root=Path(tmp_path), per_game=True)


def test_search_counts_every_way_to_fill_the_gap(isolated):
    plan = shortfall.find(FakeTwitch(), request(isolated))
    assert [c["id"] for c in plan.base] == ["c1", "c2", "c3"]
    assert plan.missing == 7
    options = {key: count for key, _label, count in plan.options()}
    assert options == {"older": 5, "lengths": 1, "non_english": 1, "talking": 1}


def test_nothing_is_added_without_a_yes(isolated):
    plan = shortfall.find(FakeTwitch(), request(isolated))
    assert [c["id"] for c in plan.choose(())] == ["c1", "c2", "c3"]
    older = plan.choose(("older",))
    assert len(older) == 8 and older[3]["id"] == "c10"     # best older clip first
    everything = plan.choose(("older", "lengths", "non_english", "talking"))
    assert len(everything) == 10 and len({c["id"] for c in everything}) == 10


def test_fills_stop_at_the_number_asked_for(isolated):
    plan = shortfall.find(FakeTwitch(), request(isolated, wanted=5))
    assert len(plan.choose(("older", "lengths"))) == 5


def test_enough_clips_means_no_questions(isolated, monkeypatch):
    asked, downloaded = [], []
    monkeypatch.setattr(session, "download_plan",
                        lambda api, plan, fills=(): downloaded.append(list(fills)) or "done")
    result = session.run_session(FakeTwitch(), request(isolated, wanted=3),
                                 lambda question, kind: asked.append(kind) or True)
    assert result == "done" and asked == [] and downloaded == [[]]


def test_the_command_line_asks_one_by_one(isolated, monkeypatch):
    asked, downloaded = [], []
    monkeypatch.setattr(session, "download_plan",
                        lambda api, plan, fills=(): downloaded.append(list(fills)) or "done")
    session.run_session(FakeTwitch(), request(isolated),
                        lambda question, kind: asked.append(kind) or kind in ("older",
                                                                              "non_english"))
    assert asked == ["older", "lengths", "non_english", "talking"]
    assert downloaded == [["older", "non_english"]]


def test_a_30_day_run_has_no_older_option(isolated):
    plan = shortfall.find(FakeTwitch(), request(isolated, hours=24 * 30))
    assert "older" not in {key for key, _l, _c in plan.options()}


def test_the_page_asks_before_downloading(isolated, monkeypatch):
    """The web page: the search job ends with the plan, nothing downloaded; the
    card downloads only the ticked fills."""
    from clipdl import ui_shortfall
    downloaded = []
    monkeypatch.setattr(ui_shortfall, "download_plan",
                        lambda api, plan, fills=(): downloaded.append(list(fills)) or
                        session.DownloadResult(0))
    monkeypatch.setattr(ui_shortfall, "ffmpeg_ready", lambda request: True)
    result = ui_shortfall.search_then_download(FakeTwitch(), request(isolated), False)
    assert isinstance(result, shortfall.Plan) and downloaded == []
    ui_shortfall.finish(None, result, ["older"])
    assert downloaded == [["older"]]
    full = ui_shortfall.search_then_download(FakeTwitch(), request(isolated, wanted=3), False)
    assert isinstance(full, session.DownloadResult) and downloaded[-1] == []


def test_the_card_shows_counts_and_updates_the_total(isolated):
    from streamlit.testing.v1 import AppTest
    plan = shortfall.find(FakeTwitch(), request(isolated))

    class Finished:
        running, started, result = False, 1.0, plan

    def page(job):
        from clipdl import ui_shortfall
        ui_shortfall.card(None, job)

    at = AppTest.from_function(page, args=(Finished(),), default_timeout=60).run()
    assert not at.exception
    labels = [box.label for box in at.checkbox]
    assert labels[0].startswith("Older clips from the 7 days before") and "(+5)" in labels[0]
    assert at.checkbox[0].value is True and at.button[0].label == "Download 8 clips"
    at.checkbox[0].uncheck().run()
    assert at.button[0].label == "Download 3 clips"
    at.checkbox[1].check().run()
    at.checkbox[2].check().run()
    assert at.button[0].label == "Download 5 clips"


# -- minimum quality -------------------------------------------------------------------
HEIGHTS = {"c1": 2160, "c2": 1080, "c3": 1440, "c10": 2160, "c11": 720, "c12": 2160}


@pytest.fixture
def heights(isolated, monkeypatch):
    from clipdl import quality
    monkeypatch.setattr(quality, "FILE", isolated / "heights.json")
    looked_up = []

    def fake_height(url):
        clip_id = url.rsplit("/", 1)[-1]
        looked_up.append(clip_id)
        return HEIGHTS.get(clip_id, 1080)
    monkeypatch.setattr(quality, "clip_height", fake_height)
    return looked_up


def quality_request(tmp_path, wanted, min_height):
    return DownloadRequest({"id": "1", "name": "VALORANT"}, wanted, ("Last 24 hours", 24),
                           ("Most views", "views"), 10, 60, True, "new", "video",
                           save_root=Path(tmp_path), per_game=True, min_height=min_height)


def test_4k_only_keeps_4k_and_offers_the_rest(heights, isolated):
    plan = shortfall.find(FakeTwitch(), quality_request(isolated, 5, 2160))
    assert [c["id"] for c in plan.base] == ["c1"]              # the only 4K clip in the window
    options = {key: count for key, _label, count in plan.options()}
    assert options["older"] == 2                               # c10 and c12 are 4K
    assert options["quality"] == 2                             # c2 (1080p), c3 (1440p)
    assert "4K" in plan.summary()
    assert len(plan.choose(("older", "quality"))) == 5


def test_quality_is_looked_up_once_per_clip(heights, isolated):
    shortfall.find(FakeTwitch(), quality_request(isolated, 5, 1440))
    first = len(heights)
    shortfall.find(FakeTwitch(), quality_request(isolated, 5, 1440))
    assert len(heights) == first                               # remembered


def test_a_high_minimum_lifts_the_1080p_cap(isolated):
    request = DownloadRequest({"id": "1", "name": "G"}, 5, ("Last 24 hours", 24),
                              ("Most views", "views"), 10, 60, max_height=1080, min_height=2160)
    assert request.max_height == 0
    assert DownloadRequest({"id": "1", "name": "G"}, 5, ("Last 24 hours", 24),
                           ("Most views", "views"), 10, 60, max_height=1080,
                           min_height=720).max_height == 1080
