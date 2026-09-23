"""A download and a trend scan side by side: separate logs, separate Cancel."""

import time

import pytest

from clipdl import jobs, locks
from clipdl.config import STOP
from clipdl.util import say, thread_pool


@pytest.fixture
def isolated_lock(tmp_data, monkeypatch):
    monkeypatch.setattr(locks, "DOWNLOADS", locks.FileLock("downloads-test"))
    monkeypatch.setattr(locks, "PID_NOTE", tmp_data / "downloads.owner")


def work(tag, rounds):
    def run():
        for i in range(rounds):
            if STOP.is_set():
                say("%s stopped" % tag)
                return "stopped"
            with thread_pool(2) as pool:
                list(pool.map(lambda k: say("%s %d-%d" % (tag, i, k)), range(2)))
            time.sleep(0.05)
        return "done"
    return run


def wait(*running):
    deadline = time.time() + 20
    while any(job.running for job in running) and time.time() < deadline:
        time.sleep(0.05)


def test_download_and_scan_run_side_by_side(isolated_lock):
    download = jobs.start("download", "D", work("DL", 20))
    scan = jobs.start("trends", "T", work("TR", 20))
    with pytest.raises(jobs.Busy):
        jobs.start("download", "again", work("x", 1))
    time.sleep(0.3)
    scan.cancel()
    wait(download, scan)
    assert download.result == "done" and not download.cancelled
    assert scan.result == "stopped" and scan.cancelled
    assert download.lines and all(line.startswith("DL") for line in download.lines if line)
    assert all(line.startswith("TR") for line in scan.lines if line)


def test_a_new_run_is_not_stopped_by_an_old_cancel(isolated_lock):
    first = jobs.start("download", "D", work("DL", 50))
    first.cancel()
    wait(first)
    second = jobs.start("download", "D2", work("DL", 2))
    wait(second)
    assert second.result == "done"
