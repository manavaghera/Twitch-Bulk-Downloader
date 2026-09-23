"""
Stats collector  --  records Twitch, Kick and YouTube for the Game Research page
==============================================================================

The web page records while it is open. Run this to keep recording without it,
so 7- and 30-day watch hours, peaks and averages have no gaps:

    .venv\\Scripts\\python.exe scripts\\collect_stats.py            every 15 min, until closed
    .venv\\Scripts\\python.exe scripts\\collect_stats.py --once     one snapshot, then exit
    .venv\\Scripts\\python.exe scripts\\collect_stats.py --every 10

`--once` is the one to schedule in Windows Task Scheduler. `--background` is
what the web page's "Record in the background" switch starts: no window, a log
in dataecorder.log, one at a time. Running this and the web page at the same
time is safe: a snapshot is never taken twice.
Uses the Twitch credentials the rest of the project uses (data\\config.json or
TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET), and YOUTUBE_API_KEY if there is one.
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipdl import recorder, stats_collect  # noqa: E402
from clipdl.cli import stored_credentials  # noqa: E402
from clipdl.trends_cli import youtube_key  # noqa: E402


def one(credentials, every):
    started = time.time()
    summary = stats_collect.run_cycle(credentials[0], credentials[1], youtube_key(),
                                      interval_min=every, log=print)
    stamp = datetime.now().strftime("%H:%M")
    if summary is None:
        print("%s  skipped - another collector recorded moments ago" % stamp)
        return
    parts = []
    for platform in ("twitch", "kick", "youtube"):
        result = summary.get(platform)
        if result:
            parts.append("%s %s" % (platform, result.get("error") or result.get("paused") or
                                    "%d streams / %d games" % (result["streams"], result["games"])))
    print("%s  %s  (%.0f s)" % (stamp, " | ".join(parts), time.time() - started))


def main():
    parser = argparse.ArgumentParser(description="Record live stats for Game Research.")
    parser.add_argument("--once", action="store_true", help="one snapshot, then exit")
    parser.add_argument("--every", type=int, default=stats_collect.INTERVAL_MIN,
                        help="minutes between snapshots (default %(default)s)")
    parser.add_argument("--background", action="store_true",
                        help="no window: log to data/recorder.log, one recorder at a time")
    args = parser.parse_args()
    if args.background:
        return background(args.every)
    if args.every < 5:
        sys.exit("Use --every 5 or more: faster adds load without adding detail.")
    credentials = stored_credentials()
    if not credentials:
        sys.exit("No Twitch credentials: run the web page once and connect, or set "
                 "TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET.")
    if args.once:
        one(credentials, args.every)
        return
    print("Recording every %d minutes. Close this window (or Ctrl+C) to stop." % args.every)
    try:
        while True:
            one(credentials, args.every)
            time.sleep(max(30, stats_collect.next_due(args.every) - time.time() + 5))
    except KeyboardInterrupt:
        print("Stopped.")


def background(every):
    """The windowless recorder the web page starts (and Windows, at sign-in)."""
    if not recorder.claim():
        return                              # one is running already
    log = recorder.open_log()
    sys.stdout = sys.stderr = log           # pythonw has no console to print to
    print("=== recorder started %s ===" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    while not recorder.should_stop():
        credentials = stored_credentials()
        if credentials:
            try:
                one(credentials, every)
            except Exception as error:      # keep recording through a bad cycle
                print("%s  cycle failed: %s" % (datetime.now().strftime("%H:%M"), error))
        else:
            print("No Twitch credentials yet - connect on the web page.")
        wake = max(30, stats_collect.next_due(every) - time.time() + 5) if credentials else 300
        deadline = time.time() + wake
        while time.time() < deadline and not recorder.should_stop():
            time.sleep(3)
    print("=== recorder stopped %s ===" % datetime.now().strftime("%Y-%m-%d %H:%M"))


if __name__ == "__main__":
    main()
