"""
Autopilot  --  one run of the daily clip pipeline, with the saved settings
==============================================================================

Picks games, downloads their best new clips, makes Shorts (captions and the
facecam look if chosen) and writes title ideas beside every file, into
<download folder>\\Autopilot\\<today>. Settings are made on the web page's
Autopilot tab and saved in data\\autopilot.json.

    .venv\\Scripts\\python.exe scripts\\autopilot.py

The web page can schedule this every day in Windows Task Scheduler. Output
also goes to data\\autopilot.log.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipdl import autopilot  # noqa: E402
from clipdl.api import TwitchAPI, TwitchError  # noqa: E402
from clipdl.cli import stored_credentials  # noqa: E402
from clipdl.config import DATA_DIR  # noqa: E402
from clipdl.util import say, set_output_sink  # noqa: E402


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = (DATA_DIR / "autopilot.log").open("a", encoding="utf-8")

    def sink(text, end):                # say() prints to the console itself
        log.write(text + end)
        log.flush()
    set_output_sink(sink)
    say("")
    say("=== %s ===" % time.strftime("%Y-%m-%d %H:%M"))
    credentials = stored_credentials()
    if not credentials:
        say("No Twitch credentials - open the web page once and connect.")
        return 1
    try:
        autopilot.run(TwitchAPI(credentials[0], credentials[1]))
    except TwitchError as error:
        say("Stopped: %s" % error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
