"""The files several programs share: locks, the download history, the YouTube
quota count, sign-in tokens and the stats database."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from clipdl import accounts, locks, manifest, stats_db, yt_quota

SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")


# -- locks -------------------------------------------------------------------------
def test_lock_is_exclusive_across_programs(tmp_data):
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sys, time; sys.path.insert(0, %r)\n"
        "from clipdl import locks\n"
        "locks.DATA_DIR = __import__('pathlib').Path(%r)\n"
        "lock = locks.FileLock('t'); assert lock.acquire(); print('held', flush=True)\n"
        "time.sleep(3)") % (SCRIPTS, str(tmp_data))], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    mine = locks.FileLock("t")
    assert mine.busy_elsewhere()
    assert not mine.acquire()
    holder.wait()
    assert mine.acquire(timeout=5)
    mine.release()


def test_lock_is_exclusive_across_threads(tmp_data):
    first, second = locks.FileLock("x"), locks.FileLock("x")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


# -- download history ------------------------------------------------------------
class Job:
    def __init__(self, clip_id):
        self.clip_id, self.game_name, self.streamer, self.title = clip_id, "G", "s", "t"
        self.url, self.path, self.views, self.created_at = "u", "p", 1, "c"


def test_two_runs_never_lose_each_others_history(tmp_data):
    path = tmp_data / "manifest.json"
    a, b = manifest.Manifest(path), manifest.Manifest(path)
    a.record(Job("a1"), "downloaded")
    b.record(Job("b1"), "downloaded")
    a.record(Job("a2"), "failed")
    a.flush()
    b.flush()
    assert sorted(manifest.Manifest(path).data["clips"]) == ["a1", "a2", "b1"]
    assert b.status_of("a2") == "failed"          # b picked up what a recorded


# -- YouTube quota ------------------------------------------------------------------
def test_quota_costs():
    assert yt_quota.cost("https://www.googleapis.com/youtube/v3/search") == 100
    assert yt_quota.cost("https://www.googleapis.com/youtube/v3/videos") == 1
    assert yt_quota.cost("https://www.googleapis.com/upload/youtube/v3/videos", "POST") == 1600


def test_quota_refuses_to_overdraw_and_keeps_a_reserve(tmp_data):
    assert yt_quota.spend(9000, "search")
    assert not yt_quota.spend(200, "search", reserve=900)
    assert yt_quota.spend(100, "search", reserve=900)
    assert yt_quota.used_today() == 9100 and yt_quota.remaining() == 900
    yt_quota.exhausted()
    assert yt_quota.remaining() == 0 and not yt_quota.can_spend(1)


def test_web_client_does_not_call_youtube_without_quota(tmp_data):
    from clipdl.web import WebClient

    class NoNetwork:
        headers = {}

        def get(self, *args, **kwargs):
            raise AssertionError("a YouTube call was made")
    yt_quota.exhausted()
    client = WebClient()
    client.session = NoNetwork()
    assert client.get_json("https://www.googleapis.com/youtube/v3/search", {}) is None


# -- keep me signed in ---------------------------------------------------------------
def test_sign_in_tokens(tmp_data):
    accounts.set_user("alice", "password123")
    token = accounts.issue_token("Alice")
    assert accounts.verify_token(token) == "alice"
    raw, signature = token.split(".")
    assert accounts.verify_token(raw + "." + "0" * 64) is None
    assert accounts.verify_token("nonsense") is None
    assert accounts.verify_token(accounts.issue_token("alice", days=-1)) is None
    other = accounts.issue_token("alice")
    accounts.revoke_token(other)
    assert accounts.verify_token(other) is None and accounts.verify_token(token) == "alice"
    accounts.set_user("alice", "a-new-password")
    assert accounts.verify_token(token) is None     # a password change signs everyone out
    assert accounts.issue_token("nobody") is None


# -- stats database -------------------------------------------------------------------
def test_compaction_keeps_watch_hours_exact(tmp_data):
    hour = (int(time.time()) // 3600 - 24 * 10) * 3600
    samples = [(hour + 0, 600, 100), (hour + 600, 900, 400), (hour + 1500, 2100, 1000)]
    for ts, interval, viewers in samples:
        stats_db.write_cycle(ts, "twitch", interval, {"streams": 1, "viewers": viewers,
                                                       "games": 1, "seconds": 1},
                             [("g", viewers, 10, 10, viewers, viewers, 1, 1, None)], [], {}, {})
    before = sum(interval * viewers for _ts, interval, viewers in samples)
    stats_db.compact(hour + 3600)
    rows = stats_db.query("SELECT g.viewers, r.interval_s FROM game_samples g JOIN runs r"
                          " ON r.ts = g.ts AND r.platform = g.platform")
    assert len(rows) == 1
    viewers, interval = rows[0]
    assert interval == 3600
    assert abs(viewers * interval - before) <= interval      # rounding only


def test_claim_cycle_only_once(tmp_data):
    assert stats_db.claim_cycle("last_cycle", 600)[0]
    assert not stats_db.claim_cycle("last_cycle", 600)[0]


@pytest.mark.parametrize("platform", ["kick"])
def test_health_flags_a_broken_platform(tmp_data, platform):
    import json
    from clipdl import stats_collect
    stats_db.set_meta("status_" + platform, json.dumps({"error": "HTTP 403", "at": 1}))
    problems = stats_collect.health([platform])
    assert problems and "HTTP 403" in problems[0][1]
    stats_db.set_meta("status_" + platform, json.dumps({"streams": 3000, "games": 90, "at": 1}))
    assert stats_collect.health([platform]) == []
    stats_db.set_meta("status_" + platform, json.dumps({"streams": 4, "games": 1, "at": 1}))
    assert "only 4" in stats_collect.health([platform])[0][1]
