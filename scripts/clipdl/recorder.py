"""The background recorder: Game research stats with the web page closed.

The page records while it is open; 7- and 30-day numbers need the hours it is
not. This runs scripts/collect_stats.py as a small program of its own, with no
window, and can start it with Windows: a per-user "Run" entry (the same place
any app's "open at sign-in" setting uses - no admin rights, listed under Task
Manager > Startup apps, removed again by the same switch).

Only one recorder runs at a time (it holds data/recorder.lock), and running it
beside the page is safe: a snapshot is never taken twice.
"""

import subprocess
import sys
import time
from pathlib import Path

from .config import DATA_DIR, SCRIPT_DIR
from .locks import FileLock

LOCK = FileLock("recorder")
LOG = DATA_DIR / "recorder.log"
STOP_FILE = DATA_DIR / "recorder.stop"
STARTED = DATA_DIR / "recorder.started"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ENTRY = "ClipStudioRecorder"
SCRIPT = SCRIPT_DIR / "collect_stats.py"


def _python():
    """pythonw.exe beside this Python when there is one: no console window."""
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    return windowless if windowless.exists() else python


def command():
    return '"%s" "%s" --background' % (_python(), SCRIPT)


# -- starting with Windows -------------------------------------------------------------
def at_startup():
    """True when the recorder starts by itself at sign-in."""
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, ENTRY)
        return True
    except OSError:
        return False


def set_startup(on):
    """Add or remove the sign-in entry. Returns (ok, message)."""
    if sys.platform != "win32":
        return False, ("Starting with the computer is set up from here on Windows only. "
                       "Elsewhere, add '@reboot python scripts/collect_stats.py' to crontab.")
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, ENTRY, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(key, ENTRY)
                except FileNotFoundError:
                    pass
    except OSError as error:
        return False, "Windows said no: %s" % error
    return True, ("The recorder now starts when you sign in to Windows." if on
                  else "The recorder no longer starts with Windows.")


# -- running now -----------------------------------------------------------------------
def running():
    """True while a background recorder runs (the page's own recording aside)."""
    return LOCK.busy_elsewhere()


def since():
    try:
        return float(STARTED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def start_now():
    """Start the recorder now, detached from the page. Returns (ok, message)."""
    if running():
        return True, "Already recording in the background."
    try:
        STOP_FILE.unlink()
    except OSError:
        pass
    flags = 0
    if sys.platform == "win32":
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_NO_WINDOW)
    try:
        subprocess.Popen([str(_python()), str(SCRIPT), "--background"], cwd=str(SCRIPT_DIR),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True,
                         start_new_session=sys.platform != "win32")
    except OSError as error:
        return False, "Could not start it: %s" % error
    for _ in range(20):                     # it takes the lock within a second or two
        if running():
            return True, "Recording in the background."
        time.sleep(0.25)
    return False, "It did not start - see %s." % LOG


def stop_now():
    """Ask the running recorder to finish (it checks every few seconds)."""
    try:
        STOP_FILE.write_text(str(time.time()), encoding="utf-8")
    except OSError as error:
        return False, str(error)
    for _ in range(40):
        if not running():
            return True, "Stopped."
        time.sleep(0.25)
    return True, "Stopping after the snapshot it is taking."


# -- inside the recorder ---------------------------------------------------------------
def should_stop():
    return STOP_FILE.exists()


def claim():
    """Called by the recorder itself: True if it is the only one running."""
    if not LOCK.acquire():
        return False
    try:
        STOP_FILE.unlink()
    except OSError:
        pass
    STARTED.write_text(str(time.time()), encoding="utf-8")
    return True


def open_log():
    """The recorder's log, cut back when it grows past half a megabyte."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if LOG.exists() and LOG.stat().st_size > 512 * 1024:
        tail = LOG.read_bytes()[-128 * 1024:]
        LOG.write_bytes(tail)
    return open(LOG, "a", encoding="utf-8", buffering=1)


def log_tail(chars=3000):
    try:
        return LOG.read_text(encoding="utf-8", errors="replace")[-chars:]
    except OSError:
        return ""
