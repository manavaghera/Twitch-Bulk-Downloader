"""Where clips are saved: checking a folder the user typed, and remembering it.

The choice lives in data/preferences.json (not config.json, which holds the
Twitch secret and should stay a file nobody needs to open).

Resuming and "already have it" still work per folder, because they look at
the files in the folder a run saves to. "New clips only" works across all of
them, because it reads the download manifest, which records every clip
whatever folder it went to.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

from .config import DATA_DIR, DOWNLOAD_ROOT
from .util import load_json, sanitize, save_json

PREFS_FILE = DATA_DIR / "preferences.json"


def load_prefs():
    prefs = load_json(PREFS_FILE, {})
    return prefs if isinstance(prefs, dict) else {}


def save_prefs(**changes):
    prefs = load_prefs()
    prefs.update(changes)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_json(PREFS_FILE, prefs)


def saved_folder():
    """(root folder, one sub-folder per game?) from the last run, or the defaults."""
    prefs = load_prefs()
    return (str(prefs.get("download_folder") or DOWNLOAD_ROOT),
            bool(prefs.get("per_game_folder", True)))


def parse_folder(text):
    """(Path, None) or (None, why) - a look at the text only, nothing touched on disk.

    Used while the user is still typing, where check_folder() would create a
    folder for every half-typed path.
    """
    text = (text or "").strip().strip('"').strip("'")
    if not text:
        return None, "Type a folder, or use Browse."
    path = Path(os.path.expandvars(os.path.expanduser(text)))
    if not path.is_absolute():
        return None, "Use a full path, like C:\\Users\\you\\Videos\\Clips."
    return path, None


def check_folder(text):
    """Turn what the user typed into a usable folder. Returns (Path, None) or (None, why).

    The folder is created if it does not exist yet, and a tiny test file is
    written and removed, so a read-only or disconnected drive is caught now
    rather than as fifty failed downloads.
    """
    path, problem = parse_folder(text)
    if problem:
        return None, problem
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / (".clipdl-write-test-%s" % uuid.uuid4().hex[:8])
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as error:
        return None, "Cannot save there: %s" % (error.strerror or error)
    return path.resolve(), None


def game_folder(root, game_name, per_game=True):
    """The folder one run saves into."""
    root = Path(root)
    return root / sanitize(game_name or "clips", 60) if per_game else root


_PICKER = r"""
import sys, tkinter
from tkinter import filedialog
root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
chosen = filedialog.askdirectory(initialdir=sys.argv[1] or None,
                                 title="Choose where to save the clips")
sys.stdout.write(chosen or "")
"""


def pick_folder(start=""):
    """Open the system folder picker and return the chosen path, or "" if cancelled.

    Runs in its own process: a Tk window inside the web server's threads is a
    well-known way to freeze it. This only makes sense when the page runs on
    the same computer as the browser, which is why the web page hides the
    button once it is hosted.
    """
    try:
        done = subprocess.run([sys.executable, "-c", _PICKER, start],
                              capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""
