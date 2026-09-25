"""Small shared helpers: printing, prompts, file names, JSON on disk."""

import json
import os
import re
import sys
import threading
import unicodedata

from concurrent.futures import ThreadPoolExecutor

from .config import MAX_TITLE_CHARS, current_kind, set_kind

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
_print_lock = threading.Lock()

# The web UI sets a function per kind of job (download, trends) that collects
# every line for the page to show; say() picks the one of the thread it runs
# on, so two jobs side by side never mix their logs. Empty on the command line,
# where the console is enough (or one sink for kind None, like the Autopilot's log).
_sinks = {}


def set_output_sink(sink, kind=None):
    """Send say() calls of `kind` threads to `sink(text, end)` as well, or stop (None)."""
    if sink is None:
        _sinks.pop(kind, None)
    else:
        _sinks[kind] = sink


def thread_pool(workers):
    """A thread pool whose threads work for the same job as the calling thread."""
    return ThreadPoolExecutor(max_workers=workers, initializer=set_kind,
                              initargs=(current_kind(),))


def say(message="", end="\n"):
    """print() that is safe to call from several download threads at once."""
    with _print_lock:
        try:
            print(message, end=end, flush=True)
        except UnicodeEncodeError:
            # Last resort if the console still refuses a character.
            print(str(message).encode("ascii", "replace").decode("ascii"), end=end,
                  flush=True)
        sink = _sinks.get(current_kind()) or _sinks.get(None)
        if sink is not None:
            sink(str(message), end)


def ask(prompt):
    """input() that turns a closed stdin (piped input, Ctrl+Z) into a clean exit."""
    try:
        return input(prompt)
    except EOFError:
        sys.exit("\nInput stream closed - nothing to do, exiting.")


def ask_int(prompt, low, high, default=None):
    """Keep asking until the user types a whole number inside [low, high]."""
    while True:
        raw = ask(prompt).strip()
        if not raw and default is not None:
            return default
        if raw.isdigit() and low <= int(raw) <= high:
            return int(raw)
        say("  Please type a whole number between %d and %d." % (low, high))


def ask_yes_no(prompt, default=False):
    """Yes/no question. Pressing Enter accepts the default."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        raw = ask(prompt + suffix).strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        say("  Please answer y or n.")


# Characters Windows forbids in a file name, plus '%', which yt-dlp would read
# as the start of an output-template placeholder such as %(title)s.
FORBIDDEN_CHARS = set('\\/:*?"<>|%')
# Device names Windows reserves; no file may be called any of these.
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"}
RESERVED_NAMES |= {"COM%d" % i for i in range(1, 10)}
RESERVED_NAMES |= {"LPT%d" % i for i in range(1, 10)}


def sanitize(text, max_len=MAX_TITLE_CHARS):
    """Turn a clip title or streamer name into a safe Windows file name part."""
    # Control characters (tabs, newlines, the odd \x07 someone put in a title)
    # become spaces rather than vanishing, so words do not get glued together.
    cleaned = "".join(
        " " if ord(ch) < 32 else ch
        for ch in str(text) if ch not in FORBIDDEN_CHARS
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()  # collapse runs of whitespace
    cleaned = cleaned.strip(" .")                   # Windows strips these anyway
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" .")
    if cleaned.upper().split(".")[0] in RESERVED_NAMES:
        cleaned = "_" + cleaned
    return cleaned or "clip"


def short_title(text, limit=32):
    """A title cut down for a file name: no emoji, safe characters, whole words,
    at most `limit` characters."""
    kept = "".join(ch for ch in str(text) if ch != "️" and (
        unicodedata.category(ch) not in ("So", "Sk", "Cs", "Co", "Cn", "Cf") or ch.isalnum()))
    kept = sanitize(kept, 400)
    if len(kept) > limit:
        cut = kept[:limit + 1]
        kept = cut.rsplit(" ", 1)[0] if " " in cut[limit // 2:] else kept[:limit]
    kept = kept.rstrip(" .,-_([{")
    if kept.count("(") > kept.count(")"):         # a bracket the cut left open
        kept = kept.rsplit("(", 1)[0].rstrip(" .,-_")
    return kept or "clip"


def describe_height(height):
    """'1080p', or 'unknown' when yt-dlp did not say what it fetched."""
    return ("%dp" % height) if height else "unknown quality"


def human_size(num_bytes):
    """1234567 -> '1.2 MB'."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return "%.1f %s" % (size, unit)
        size /= 1024


def chunked(items, size):
    """Yield lists of at most `size` items (Twitch caps batch lookups at 100)."""
    for start in range(0, len(items), size):
        yield items[start:start + size]


def load_json(path, default):
    """Read a JSON file, returning `default` when it is missing or corrupt."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        say("  Note: could not read %s (%s) - starting that file fresh." % (path.name, exc))
        return default


def save_json(path, payload):
    """Write JSON atomically, so a crash mid-write cannot corrupt the file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(temp, path)  # atomic on Windows and POSIX
    except OSError as exc:
        say("  Warning: could not save %s (%s)." % (path.name, exc))
