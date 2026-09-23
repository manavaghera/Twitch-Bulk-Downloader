"""Autopilot: every morning, a folder of ready-to-post Shorts - no clicking.

One run:
  1. picks the games: the top games rising in trend research, a fixed list,
     or no games at all and the fastest clips on the clip radar instead
  2. downloads each game's best new clips (trending, 10-60 s, gameplay,
     never one you already have, only streamers your permission list allows)
  3. makes the Shorts - with captions and the facecam look if chosen
  4. writes title ideas, a description and hashtags beside every file
  5. leaves a short report in the day's folder

Settings live in data/autopilot.json. Run it from the web page, or on a timer
with scripts/autopilot.py - the page can put that in Windows Task Scheduler.
"""

import json
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from . import alerts, cleanup, radar
from .config import DATA_DIR, SCRIPT_DIR, STOP
from .folders import saved_folder
from .permissions import allow_filter
from .session import DownloadRequest, download_clips, run_session
from .util import load_json, sanitize, save_json, say

SETTINGS_FILE = DATA_DIR / "autopilot.json"
LAST_FILE = DATA_DIR / "autopilot_last.json"
TASK_NAME = "ClipStudioAutopilot"
DEFAULTS = {"source": "rising", "games": 3, "game_list": [], "clips_per_game": 5,
            "radar_clips": 15, "hours": 24, "output": "short", "style": "blur",
            "captions": False, "streamer_mode": "not_blocked", "time": "09:00"}
SOURCES = {"rising": "Games rising in trend research", "list": "My own list of games",
           "mine": "Games that do best on my channel",
           "radar": "The fastest clips on the clip radar (any game)"}


def settings():
    saved = load_json(SETTINGS_FILE, {})
    return dict(DEFAULTS, **(saved if isinstance(saved, dict) else {}))


def save_settings(values):
    save_json(SETTINGS_FILE, dict(settings(), **values))


def last_run():
    data = load_json(LAST_FILE, None)
    return data if isinstance(data, dict) else None


def _pick_games(api, config):
    names = config["game_list"] if config["source"] == "list" else None
    if config["source"] == "mine":
        from .mychannel import best_games
        names = best_games(config["games"])
        if names:
            say("Your channel does best with: %s" % ", ".join(names))
        else:
            say("No channel results yet (connect it on the My channel page) - using the "
                "rising games instead.")
    if names or config["source"] == "list":
        games = []
        for name in names:
            game = api.game_by_name(name)
            if game:
                games.append({"id": game["id"], "name": game["name"]})
            else:
                say("  No Twitch category called '%s' - skipped." % name)
        return games
    from .trends_cli import research
    say("Finding the games that are rising right now (trend research)...")
    found = research(api, 60)
    picks, seen = [], set()
    # Rising first; if too few are rising, the biggest games that are not cooling.
    for trend in found["rising"] + [t for t in found["popular"] if t.verdict != "cooling"]:
        if trend.id and trend.id not in seen and len(picks) < config["games"]:
            seen.add(trend.id)
            picks.append({"id": trend.id, "name": trend.name})
    return picks


def run(api, config=None):
    """One autopilot run. Returns a summary dict (also saved to data/autopilot_last.json)."""
    config = dict(settings(), **(config or {}))
    started = time.time()
    folder = Path(saved_folder()[0]) / "Autopilot" / date.today().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    summary = {"started": started, "folder": str(folder), "source": config["source"],
               "games": [], "radar": 0, "errors": []}
    window = ("Last %d hours" % config["hours"], config["hours"])
    say("Autopilot: %s -> %s" % (SOURCES[config["source"]], folder))

    if config["source"] == "radar":
        result = radar.scan(api.client_id, api.client_secret, 50, config["hours"])
        clips = radar.filtered(result, "en", 500, hide_have=True, hide_blocked=True)
        allowed = allow_filter(config["streamer_mode"])
        # A few spare, for any the stream titles show to be talking clips.
        clips = [c for c in clips if allowed is None or allowed(c)][:config["radar_clips"] * 2]
        say("Clip radar: the best %d of %d scanned clips." % (
            min(len(clips), config["radar_clips"]), len(result["clips"])))
        if clips:
            done = download_clips(clips, folder / "Clip radar", config["output"],
                                  config["style"], config["captions"], label="Clip radar",
                                  api=api, limit=config["radar_clips"])
            summary["radar"] = _count(done)
    else:
        for game in _pick_games(api, config):
            if STOP.is_set():
                break
            request = DownloadRequest(
                game, config["clips_per_game"], window,
                ("Trending now (views per hour)", "trending"), 10, 60, True, "new",
                config["output"], config["style"], 1080, folder, True,
                captions=config["captions"], streamer_mode=config["streamer_mode"])
            try:
                # Too few clips of 10-60 s? Take other lengths; never other languages.
                result = run_session(api, request, lambda _q, kind: kind == "lengths")
                summary["games"].append([game["name"], _count(result)])
            except Exception as error:      # one game failing must not end the run
                summary["errors"].append("%s: %s" % (game["name"], str(error)[:120]))
    summary["seconds"] = round(time.time() - started)
    save_json(LAST_FILE, summary)
    _report(folder, summary)
    try:
        cleanup.auto_clean()
        alerts.autopilot_done(summary)
    except Exception as error:              # tidying up and phoning home are extras
        say("Note: %s" % error)
    say("Autopilot done in %d min - %d new clip(s) in %s."
        % (summary["seconds"] // 60, total(summary), folder))
    return summary


def _count(result):
    if not result or not result.jobs or not result.manifest:
        return 0
    return sum(1 for job in result.jobs if result.manifest.status_of(job.clip_id) == "downloaded")


def total(summary):
    return summary.get("radar", 0) + sum(n for _g, n in summary.get("games", []))


def _report(folder, summary):
    lines = ["Autopilot run, %s" % time.strftime("%d %b %Y %H:%M",
                                                 time.localtime(summary["started"])), ""]
    lines += ["%-40s %d new clip(s)" % (name, n) for name, n in summary["games"]]
    if summary["radar"]:
        lines.append("Clip radar: %d new clip(s)" % summary["radar"])
    lines += [""] + ["Problem: %s" % e for e in summary["errors"]]
    lines.append("Every video has a .txt beside it with title ideas, a description and hashtags.")
    (folder / "autopilot_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Windows Task Scheduler
# ---------------------------------------------------------------------------
def _schtasks(*args):
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    done = subprocess.run(["schtasks"] + list(args), capture_output=True, text=True,
                          creationflags=flags)
    return done.returncode == 0, (done.stdout or done.stderr or "").strip()


def schedule(at):
    """Run the autopilot every day at `at` ("HH:MM"). Returns (ok, message)."""
    if sys.platform != "win32":
        return False, ("Scheduling from here is Windows-only. Elsewhere, add a daily cron "
                       "job for scripts/autopilot.py.")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at or ""):
        return False, "Use a time like 09:00."
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")      # no console window popping up
    exe = windowless if windowless.exists() else python
    script = SCRIPT_DIR / "autopilot.py"
    ok, text = _schtasks("/Create", "/SC", "DAILY", "/TN", TASK_NAME, "/TR",
                         '"%s" "%s"' % (exe, script), "/ST", at, "/F")
    if ok:
        save_settings({"time": at})
    return ok, "Scheduled every day at %s." % at if ok else text


def unschedule():
    if sys.platform != "win32":
        return False, "Nothing to remove."
    ok, text = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    return ok, "Removed the daily run." if ok else text


def scheduled():
    """{"next": "...", "status": "..."} when the daily run is set up, else None."""
    if sys.platform != "win32":
        return None
    ok, text = _schtasks("/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V")
    if not ok:
        return None
    fields = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
    fields = {k.strip(): v.strip() for k, v in fields.items()}
    return {"next": fields.get("Next Run Time", "?"), "status": fields.get("Status", "?"),
            "last": fields.get("Last Run Time", "?")}


def settings_text(config):
    return json.dumps(config, indent=1)
