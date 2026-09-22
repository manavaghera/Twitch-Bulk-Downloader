#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Game Trend Research  --  what to clip next, and what it is worth
==============================================================================

A companion to twitch_clip_downloader.py. That one fetches clips; this one
tells you which game to point it at. Two things it answers:

  1) TRENDING GAMES   the most popular games in the US and Europe over the
                      last 7 days, and the games getting popular - new
                      releases and climbers - with fading games called out.

  2) RPM BY COUNTRY    where a thousand views is worth the most, and what each
                      of the measured games' audiences is worth per view.

Run it the same way as the downloader:

    python scripts\\twitch_trends.py

It uses the same Twitch credentials, so if the downloader already works, this
does too. Option 2 on its own needs no credentials at all.

------------------------------------------------------------------------------
WHERE THE GAMES COME FROM
------------------------------------------------------------------------------
Not from Twitch alone. Every run reads, fresh:

  Steam           the most-played chart (with last week's rank), and the
                  best-seller chart in the US, UK, Germany, France, Spain,
                  Italy, Poland and Sweden
  IGDB            the most-visited game pages - what people are looking up
  Wikipedia       page views this week vs last, English, German and French
  Twitch          live viewers by stream language, clip views week over week
  Kick            live viewers per category
  YouTube         optional: most popular gaming videos in US/UK/DE/FR. Needs
                  a free API key, saved as "youtube_api_key" in
                  data\\config.json. Without one it is skipped.

------------------------------------------------------------------------------
HOW THE LISTS ARE WORKED OUT
------------------------------------------------------------------------------
MOST POPULAR    each source is scaled against the biggest game on it, and the
                sources are averaged. The US/EU charts and US/EU streams count
                most; worldwide numbers count less.

GETTING POPULAR how fast a game is climbing, averaged over every source that
                can see it: Wikipedia and Twitch week over week, Steam rank vs
                last week, and how recently it came out. A climb only one
                source sees counts for less.

COOLING OFF     a game that blew up and is now fading can still be big for
                weeks. When its numbers are falling it is marked "cooling" and
                kept off the rising list, however big it still is.

Each run also saves a snapshot to data\\trend_history.json, so a second run a
week later compares against numbers this script actually measured.

------------------------------------------------------------------------------
TWO THINGS THESE NUMBERS ARE NOT
------------------------------------------------------------------------------
1) Twitch reports the LANGUAGE a stream is broadcast in, never where viewers
   are. No Helix endpoint gives country. So "EN%" means English-language, which
   spans the US, UK, Canada, Australia and English-speaking Europe at once.
   It is a solid read on where a game's centre of gravity sits. It is not a
   headcount of Americans.

2) The RPM figures are published industry estimates for gaming content, typed
   into scripts\\clipdl\\regions.py by hand. They are not live, and nothing here
   can see your real earnings - only YouTube Analytics can. They are for
   ranking countries against each other. Edit that file when you find better
   numbers; nothing else has to change.

Long-form and Shorts are also completely different pools: Shorts run roughly
one to two hundred times lower per view. The RPM report says so where it counts.
"""

import sys
from pathlib import Path

# Running this file directly puts <project>/scripts/ on the import path, which
# is where the clipdl package lives. Adding it explicitly as well means the
# launcher still works when it is called through a symlink or a shortcut.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipdl.api import TwitchError      # noqa: E402  (must follow the path setup)
from clipdl.trends_cli import main      # noqa: E402
from clipdl.util import say             # noqa: E402

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
