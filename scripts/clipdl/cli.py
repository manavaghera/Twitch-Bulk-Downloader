"""The menus, and the run they drive."""

import getpass
import os

from .api import TwitchAPI
from .config import (CONFIG_FILE, DATA_DIR, MAX_CLIP_SECONDS, MAX_CLIPS,
                     MIN_CLIP_SECONDS, OUTPUT_FORMATS, PROJECT_DIR, QUALITIES,
                     RANKINGS, SCRIPT_DIR, SHORT_STYLES, TIME_WINDOWS,
                     VELOCITY_MIN_SCAN)
from .folders import check_folder, save_prefs, saved_folder
from .session import DownloadRequest, known_clip_count, run_session
from .util import ask, ask_int, ask_yes_no, load_json, save_json, say

# ---------------------------------------------------------------------------
# Credentials: environment variables -> config.json -> ask once and save
# ---------------------------------------------------------------------------
def stored_credentials():
    """(client_id, client_secret, where) from the environment or a file, else None.

    Never prompts, which is what the web page needs: it shows a form instead.
    """
    env_id = os.environ.get("TWITCH_CLIENT_ID", "").strip()
    env_secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    if env_id and env_secret:
        return env_id, env_secret, "the TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET environment variables"

    # data\config.json is where we save them, but a config.json dropped in the
    # project root or next to the script is honoured too.
    for path in (CONFIG_FILE, PROJECT_DIR / "config.json", SCRIPT_DIR / "config.json"):
        config = load_json(path, None)
        if isinstance(config, dict):
            file_id = str(config.get("client_id") or "").strip()
            file_secret = str(config.get("client_secret") or "").strip()
            if file_id and file_secret:
                return file_id, file_secret, str(path)
    return None


def load_credentials():
    """Return (client_id, client_secret), prompting the user only if needed."""
    found = stored_credentials()
    if found:
        say("Using credentials from %s" % found[2])
        return found[0], found[1]

    # Nothing found, so walk the user through it once.
    say("")
    say("First run: I need your Twitch app credentials.")
    say("Get them free at https://dev.twitch.tv/console -> Register Your Application")
    say("  OAuth Redirect URL: http://localhost    Category: Application Integration")
    say("")
    client_id = ""
    while not client_id:
        client_id = ask("Client ID: ").strip()
    client_secret = ""
    while not client_secret:
        # getpass hides the typing so the secret does not end up in console
        # history or in a screen share. Pasting still works.
        client_secret = getpass.getpass("Client Secret (hidden while you type): ").strip()

    if ask_yes_no("Save these to %s so I stop asking?" % CONFIG_FILE, default=True):
        save_json(CONFIG_FILE, {"client_id": client_id, "client_secret": client_secret})
        say("Saved. It is plain text, so do not share or commit that file.")
    return client_id, client_secret


# ---------------------------------------------------------------------------
# Interactive menus
# ---------------------------------------------------------------------------
def resolve_game_name(api, name):
    """Find a category by typed name: exact match first, then suggestions."""
    say("  Looking up '%s'..." % name)
    exact = api.game_by_name(name)
    if exact:
        say("  Found: %s" % exact.get("name", name))
        return exact

    matches = api.search_categories(name, 10)
    if not matches:
        say("  Nothing on Twitch matches '%s'. Check the spelling and try again." % name)
        return None

    say("  No exact match. Closest categories on Twitch:")
    for index, match in enumerate(matches, 1):
        say("    %2d) %s" % (index, match.get("name", "?")))
    say("     0) type a different name")
    choice = ask_int("  Pick a number: ", 0, len(matches))
    return matches[choice - 1] if choice else None


def choose_game(api):
    """Menu 1: the current top categories, or type a name."""
    say("")
    say("Fetching the top categories on Twitch right now...")
    games = api.top_games(20)
    say("")
    if games:
        say("Top categories on Twitch:")
        for index, game in enumerate(games, 1):
            say("  %2d) %s" % (index, game.get("name", "?")))
    else:
        say("Twitch returned no top categories, but you can still type a name.")
    say("   0) Type a game name instead")
    say("")

    while True:
        raw = ask("Pick a number, or just type a game name: ").strip()
        if not raw:
            continue
        if raw.isdigit():
            choice = int(raw)
            if choice == 0:
                # Explicit "type a name" branch.
                while True:
                    typed = ask("Game / category name: ").strip()
                    if not typed:
                        continue
                    game = resolve_game_name(api, typed)
                    if game:
                        return game
                continue
            if 1 <= choice <= len(games):
                return games[choice - 1]
            say("  That number is not on the list.")
            continue
        # Anything non-numeric is treated as a game name straight away.
        game = resolve_game_name(api, raw)
        if game:
            return game


def choose_time_window():
    """Menu 3: how far back to look."""
    say("")
    say("Time window for the clips:")
    for index, (label, _hours) in enumerate(TIME_WINDOWS, 1):
        say("  %d) %s" % (index, label))
    choice = ask_int("Pick a number [1-%d]: " % len(TIME_WINDOWS), 1, len(TIME_WINDOWS))
    return TIME_WINDOWS[choice - 1]


def choose_ranking(window_label):
    """Menu 4: most-viewed of the whole window, or what is taking off now."""
    say("")
    say("Rank the clips by:")
    say("  1) Most views    - the biggest clips of the %s" % window_label.lower())
    say("  2) Trending now  - views per hour, so a clip from this morning can")
    say("                     beat a tired one that has had all week to gather")
    say("                     views. Reads at least %d clips before ranking, so"
        % VELOCITY_MIN_SCAN)
    say("                     it is slower than option 1.")
    choice = ask_int("Pick a number [1-2, Enter for 1]: ", 1, 2, default=1)
    return RANKINGS[choice - 1]


def choose_clip_length():
    """Menu 5: how long a clip has to be. Returns (min seconds, max seconds)."""
    say("")
    say("Clip length:")
    say("  1) %d to %d seconds - long enough to edit with, short enough to keep pace"
        % (MIN_CLIP_SECONDS, MAX_CLIP_SECONDS))
    say("  2) Any length        - take whatever Twitch has")
    say("  3) Pick my own range")
    choice = ask_int("Pick a number [1-3, Enter for 1]: ", 1, 3, default=1)
    if choice == 2:
        return 0, 0
    if choice == 3:
        low = ask_int("  Shortest clip in seconds [1-600]: ", 1, 600,
                      default=MIN_CLIP_SECONDS)
        high = ask_int("  Longest clip in seconds [%d-600]: " % low, low, 600,
                       default=max(low, MAX_CLIP_SECONDS))
        return low, high
    return MIN_CLIP_SECONDS, MAX_CLIP_SECONDS


def choose_output_format():
    """Menu 7: a normal video, a vertical Short, or both. Returns (output, style)."""
    say("")
    say("Format:")
    for index, (label, _key) in enumerate(OUTPUT_FORMATS, 1):
        say("  %d) %s" % (index, label))
    choice = ask_int("Pick a number [1-%d, Enter for 1]: " % len(OUTPUT_FORMATS),
                     1, len(OUTPUT_FORMATS), default=1)
    output = OUTPUT_FORMATS[choice - 1][1]
    if output == "video":
        return output, "blur"

    say("")
    say("How should the 16:9 clip fill a 9:16 screen?")
    for index, (label, _key) in enumerate(SHORT_STYLES, 1):
        say("  %d) %s" % (index, label))
    choice = ask_int("Pick a number [1-%d, Enter for 1]: " % len(SHORT_STYLES),
                     1, len(SHORT_STYLES), default=1)
    return output, SHORT_STYLES[choice - 1][1]


def choose_quality():
    """Menu 8: capped at 1080p, or the most each clip has. Returns a height cap."""
    say("")
    say("Quality:")
    for index, (label, _height) in enumerate(QUALITIES, 1):
        say("  %d) %s" % (index, label))
    choice = ask_int("Pick a number [1-%d, Enter for 1]: " % len(QUALITIES),
                     1, len(QUALITIES), default=1)
    return QUALITIES[choice - 1][1]


def choose_folder():
    """Menu 9: where to save. Returns (root folder, one sub-folder per game?)."""
    last, per_game = saved_folder()
    say("")
    while True:
        typed = ask("Save to which folder? [Enter for %s]: " % last).strip() or last
        folder, problem = check_folder(typed)
        if folder:
            break
        say("  %s" % problem)
    per_game = ask_yes_no("Put each game in its own sub-folder?", default=per_game)
    save_prefs(download_folder=str(folder), per_game_folder=per_game)
    return folder, per_game


def choose_history_mode(known_count, game_name, wanted):
    """Menu 6: what to do about clips earlier runs already fetched.

    Skipped entirely the first time a game is downloaded, because with no
    history there is nothing to decide.
    """
    if not known_count:
        return "new"

    say("")
    say("You have already downloaded %d clip(s) of %s on an earlier run."
        % (known_count, game_name))
    say("  1) New clips only  - skip those %d, so all %d are clips you have never had"
        % (known_count, wanted))
    say("  2) Include them    - re-download any whose file you have deleted")
    say("")
    say("     Pick 2 if you lost the files and want them back.")
    say("     Pick 1 for a fresh batch to add to the ones you already keep.")
    choice = ask_int("Pick a number [1-2, Enter for 1]: ", 1, 2, default=1)
    return "new" if choice == 1 else "include"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv, help_text=""):
    """Run one download session. `help_text` is the launcher's setup notes."""
    if any(arg in ("-h", "--help", "/?") for arg in argv[1:]):
        print(help_text or __doc__)
        return 0

    say("=" * 66)
    say("  Twitch Top Clips Downloader")
    say("=" * 66)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    client_id, client_secret = load_credentials()
    api = TwitchAPI(client_id, client_secret)
    api.token()  # fail fast, with a clear message, if the credentials are wrong

    # 1) which game / category
    game = choose_game(api)

    # 2) how many clips
    say("")
    wanted = ask_int("How many clips do you want? [1-%d, Enter for 50]: " % MAX_CLIPS,
                     1, MAX_CLIPS, default=50)

    # 3) which time window
    window_label, window_hours = choose_time_window()

    # 4) how to rank what the search finds
    ranking_label, ranking = choose_ranking(window_label)

    # 5) how long a clip has to be to be worth having
    min_seconds, max_seconds = choose_clip_length()

    # 6) gameplay only, or everything Twitch files under this category
    say("")
    gameplay_only = ask_yes_no(
        "Gameplay clips only (skip caster desks, watch parties and chat clips)?",
        default=True)

    # 7) a normal video, a vertical Short, or both
    output, short_style = choose_output_format()

    # 8) how high a resolution, and 9) where to save
    max_height = choose_quality()
    save_root, per_game = choose_folder()

    # 10) what to do about clips earlier runs already fetched. The answer
    # decides which clips the search is even allowed to return.
    known = known_clip_count(game.get("name", "?"))
    history_mode = choose_history_mode(known, game.get("name", "?"), wanted)

    request = DownloadRequest(game, wanted, (window_label, window_hours),
                              (ranking_label, ranking), min_seconds, max_seconds,
                              gameplay_only, history_mode, output, short_style,
                              max_height, save_root, per_game)
    return run_session(api, request, lambda question, _kind: ask_yes_no(question)).code
