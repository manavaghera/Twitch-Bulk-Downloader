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
