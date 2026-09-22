#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Twitch Top Clips Downloader  --  beginner friendly, Windows first
==============================================================================

This file is just the launcher. The code lives beside it, in scripts/clipdl/:

    config.py    settings and paths - the file to open when tweaking behaviour
    util.py      printing, prompts, file names, JSON on disk
    api.py       the Twitch Helix client (tokens, retries, endpoints)
    manifest.py  the per-clip log that survives between runs
    filter.py    searching, the gameplay filter, de-duplication, ranking
    download.py  turning chosen clips into files
    report.py    everything printed after the work is done
    cli.py       the menus, and the run they drive

What it does: you pick a Twitch game/category, it grabs the best clips from the
last 24 hours / 7 days / 30 days, keeps only clips from ENGLISH channels, throws
out the ones that are people talking rather than playing (caster desks, watch
parties, patch-note reactions), throws out repeats of a moment it already has,
and downloads the rest with yt-dlp as .mp4 at up to 1080p (whatever the streamer
broadcast at).

"Best" is your choice of two:

    Most views     the biggest clips of the whole window.
    Trending now   views per hour, so a clip from this morning can beat one
                   that has had all week to gather views. Reads a far larger
                   pool before ranking, so it takes longer.

------------------------------------------------------------------------------
WINDOWS SETUP (one time)
------------------------------------------------------------------------------
1) Install Python 3.8+ from https://www.python.org/downloads/windows/
   Tick "Add python.exe to PATH" in the installer.

2) Install the two libraries this script needs:

       pip install requests yt-dlp

   If "pip" is not recognised, use the py launcher instead:

       py -m pip install requests yt-dlp

3) Get a Twitch Client ID + Client Secret (free, about 2 minutes):
   - Open https://dev.twitch.tv/console and log in with your Twitch account.
     (Twitch requires two-factor auth on the account before it will let you
     register an app, so turn on 2FA in your Twitch security settings first.)
   - Applications  ->  Register Your Application
   - Name:               anything unique, e.g. "my-clip-downloader"
   - OAuth Redirect URL: http://localhost
   - Category:           Application Integration
   - Client Type:        Confidential
   - Click Create, then Manage on your new app.
   - Copy the Client ID. Click "New Secret" and copy the Client Secret.
     The secret is shown only once, so paste it somewhere safe.

4) Hand the credentials to the script, either way:
   - Just run the script: it asks once, then saves them to data\\config.json
     so it never asks again.
   - Or set environment variables first (PowerShell):
         $env:TWITCH_CLIENT_ID     = "your_client_id"
         $env:TWITCH_CLIENT_SECRET = "your_client_secret"

------------------------------------------------------------------------------
EXAMPLE RUNS  (PowerShell, from the project folder)
------------------------------------------------------------------------------
    cd "C:\\path\\to\\Twitch-Bulk-Downloader"
    python scripts\\twitch_clip_downloader.py

    # if "python" is not recognised, use the py launcher:
    py scripts\\twitch_clip_downloader.py

    # credentials for this run only, nothing written to disk:
    $env:TWITCH_CLIENT_ID="abc"; $env:TWITCH_CLIENT_SECRET="xyz"; py scripts\\twitch_clip_downloader.py

    # print these setup notes again:
    py scripts\\twitch_clip_downloader.py --help

Clips land in    <project>\\downloads\\<Game Name>\\001_Streamer_(Clip title).mp4
Bookkeeping in   <project>\\data\\   (token cache, language + stream-title caches, manifest)

Re-running the same game and window is safe. When a game has been downloaded
before, the script asks what to do with those earlier clips:

    1) New clips only - skips them, so asking for 100 gets you 100 clips you
       have never had. The new batch is numbered on from the last one.
    2) Include them   - the old behaviour: anything whose file you deleted is
       downloaded again, anything still on disk is left alone.

If a run cannot reach the number you asked for, the end of the run says how
many clips the window actually held, how many you already had, and how many
are missing.
"""

import sys
from pathlib import Path

# Running this file directly puts <project>\scripts\ on the import path, which
# is where the clipdl package lives. Adding it explicitly as well means the
# launcher still works when it is called through a symlink or a shortcut.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipdl.api import TwitchError   # noqa: E402  (must follow the path setup)
from clipdl.cli import main          # noqa: E402
from clipdl.util import say          # noqa: E402

if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv, help_text=__doc__))
    except TwitchError as error:
        # Expected, explainable problems: show the message, not a traceback.
        say("")
        say("Problem: %s" % error)
        sys.exit(1)
    except KeyboardInterrupt:
        say("")
        say("Cancelled.")
        sys.exit(130)
