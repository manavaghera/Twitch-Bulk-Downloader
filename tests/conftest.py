"""Shared test setup: import the project from scripts/, and keep every file a
test writes in a temporary folder instead of data/."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """Point the modules' data files at a temporary folder."""
    from clipdl import accounts, locks, manifest, permissions, stats_db, yt_quota
    monkeypatch.setattr(accounts, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(accounts, "SECRET_FILE", tmp_path / "session_secret")
    monkeypatch.setattr(accounts, "REVOKED_FILE", tmp_path / "revoked.json")
    monkeypatch.setattr(accounts, "ITERATIONS", 1000)       # fast hashes for tests
    monkeypatch.setattr(permissions, "FILE", tmp_path / "permissions.json")
    monkeypatch.setattr(stats_db, "DB_FILE", tmp_path / "stats.db")
    monkeypatch.setattr(yt_quota, "FILE", tmp_path / "youtube_quota.json")
    monkeypatch.setattr(locks, "DATA_DIR", tmp_path)
    monkeypatch.setattr(manifest, "_FILE_LOCK", locks.FileLock("manifest-test"))
    return tmp_path


@pytest.fixture(autouse=True)
def private_timings(tmp_path_factory, monkeypatch):
    """Tests run on fake servers and tiny videos: their timings must never reach
    the estimates the app shows for real runs."""
    from clipdl import captions, timing
    folder = tmp_path_factory.mktemp("settings")
    monkeypatch.setattr(timing, "FILE", folder / "timings.json")
    monkeypatch.setattr(captions, "SETTINGS", folder / "captions.json")


@pytest.fixture(autouse=True)
def no_real_browser_cookies(monkeypatch):
    """No test may read the cookies of a browser on this PC."""
    import yt_dlp.cookies

    def refuse(browser, **kwargs):
        raise AssertionError("a test tried to read %s's real cookies" % browser)
    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", refuse)
