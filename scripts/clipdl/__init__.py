"""Twitch clip downloader, split into small modules.

Importing the package checks that Python and the two third-party libraries are
new enough and present, so a missing install produces one clear line instead of
a stack trace half a second later.

    config    settings and paths, the things worth tweaking
    util      printing, prompts, file names, JSON on disk
    api       the Twitch Helix client
    manifest  the per-clip log that survives between runs
    filter    searching, the gameplay filter, de-duplication, ranking
    download  turning clips into files
    report    everything printed after the work is done
    cli       the menus and the run itself
"""
# ---------------------------------------------------------------------------
# Dependency checks up front, so a beginner gets an instruction, not a
# stack trace.
# ---------------------------------------------------------------------------
import sys
from importlib.util import find_spec

if sys.version_info < (3, 8):
    sys.exit("This script needs Python 3.8 or newer. You have %s." % sys.version.split()[0])

for module, install in (("requests", "requests"), ("yt_dlp", "yt-dlp")):
    if find_spec(module) is None:
        sys.exit("Missing library '%s'.  Fix it with:   pip install %s" % (module, install))

# Clip titles are full of emoji and non-Latin text. Older Windows consoles use a
# legacy code page and raise UnicodeEncodeError when asked to print those, so
# switch stdout to UTF-8 and replace anything that still cannot be shown.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # very old Python, or a stream that does not support it
    pass
