"""Disk cleanup: old dated folders go to the Recycle Bin.

The Autopilot makes a folder a day (Autopilot\\2026-09-23), and so do the
clip radar and the streamer downloads (<name>\\<date>). Clips are posted
within days; weeks later they only fill the disk. This finds folders named by
a date older than N days - never anything else - and moves them to the Recycle
Bin, so a mistake can still be undone. The download history keeps knowing
those clips, so they are not downloaded again.
"""

import re
from datetime import date, datetime
from pathlib import Path

from .config import DATA_DIR
from .folders import saved_folder
from .util import human_size, load_json, save_json, say

SETTINGS = DATA_DIR / "cleanup.json"
DEFAULTS = {"days": 14, "auto": False}
DATED = re.compile(r"\d{4}-\d{2}-\d{2}")


def settings():
    data = load_json(SETTINGS, {})
    return dict(DEFAULTS, **(data if isinstance(data, dict) else {}))


def save_settings(**changes):
    save_json(SETTINGS, dict(settings(), **changes))


def _size(folder):
    total = count = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
                count += 1
        except OSError:
            pass
    return total, count


def old_folders(days, root=None):
    """[{path, day, age, bytes, files}] of dated folders at least `days` old, oldest
    first: <root>\\<anything>\\<date> - the Autopilot, clip radar and streamer
    folders. A game folder's own clips are never dated folders, so never touched."""
    root = Path(root or saved_folder()[0])
    if not root.is_dir():
        return []
    today, found = date.today(), []
    for parent in [p for p in root.iterdir() if p.is_dir()]:
        try:
            children = [c for c in parent.iterdir() if c.is_dir()]
        except OSError:
            continue
        for folder in children:
            if not DATED.fullmatch(folder.name):
                continue
            try:
                day = datetime.strptime(folder.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            age = (today - day).days
            if age >= days:
                size, files = _size(folder)
                found.append({"path": folder, "day": day, "age": age, "bytes": size,
                              "files": files})
    return sorted(found, key=lambda f: f["day"])


def to_recycle_bin(folders):
    """Move the folders to the Recycle Bin. Returns (moved, bytes, [problems])."""
    from send2trash import send2trash
    moved, freed, problems = 0, 0, []
    for entry in folders:
        path = Path(entry["path"])
        if not DATED.fullmatch(path.name):          # never anything but a dated folder
            problems.append("%s is not a dated folder - left alone." % path)
            continue
        try:
            send2trash(str(path))
        except Exception as error:                  # in use, permissions, no Recycle Bin
            problems.append("%s: %s" % (path, error))
            continue
        moved += 1
        freed += entry.get("bytes", 0)
    return moved, freed, problems


def auto_clean():
    """After an Autopilot run, when switched on: tidy up by itself."""
    config = settings()
    if not config["auto"]:
        return
    old = old_folders(config["days"])
    if not old:
        return
    moved, freed, problems = to_recycle_bin(old)
    say("Cleanup  : %d folder(s) older than %d days moved to the Recycle Bin (%s)."
        % (moved, config["days"], human_size(freed)))
    for problem in problems:
        say("  " + problem)
