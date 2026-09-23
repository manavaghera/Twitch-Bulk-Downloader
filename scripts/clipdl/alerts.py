"""Phone alerts: a Discord or Telegram message when something needs you.

  spike       a game is far above its usual viewers for this hour
  live        a streamer you follow just went live
  launch      a most-wishlisted game launches tomorrow or today
  autopilot   the daily Autopilot run finished (or had problems)

Discord: a channel's webhook address (Channel settings > Integrations >
Webhooks > New webhook > Copy URL). Telegram: a bot token from @BotFather and
your chat id. Both are kept in data/alerts.json, on this PC only. Checks run
with each stats snapshot (the page or the background recorder); every alert
is sent once - data/alerts_sent.json remembers what went out.
"""

import re
import time
from datetime import datetime

import requests

from .config import DATA_DIR, HTTP_TIMEOUT
from .locks import FileLock
from .util import load_json, save_json

SETTINGS = DATA_DIR / "alerts.json"
SENT = DATA_DIR / "alerts_sent.json"
EVENTS = {"spike": "A game spikes far above its usual viewers",
          "live": "A streamer I follow goes live",
          "launch": "A big launch is tomorrow or today",
          "autopilot": "The Autopilot finished"}
DEFAULTS = {"discord": "", "telegram_token": "", "telegram_chat": "",
            "events": {name: True for name in EVENTS}, "spike_jump": 2.5,
            "spike_min_viewers": 2000}
DISCORD_URL = re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+")
TELEGRAM_TOKEN = re.compile(r"\d{5,}:[\w-]{30,}")
TELEGRAM_CHAT = re.compile(r"-?\d{3,}|@[A-Za-z0-9_]{5,}")
_sent_lock = FileLock("alerts")


def settings():
    data = load_json(SETTINGS, {})
    data = data if isinstance(data, dict) else {}
    merged = dict(DEFAULTS, **data)
    merged["events"] = dict(DEFAULTS["events"], **(data.get("events") or {}))
    return merged


def save_settings(**changes):
    """Validated save. Returns a problem to show, or None."""
    config = dict(settings(), **changes)
    if config["discord"] and not DISCORD_URL.fullmatch(config["discord"]):
        return "That is not a Discord webhook address (https://discord.com/api/webhooks/...)."
    if config["telegram_token"] and not TELEGRAM_TOKEN.fullmatch(config["telegram_token"]):
        return "That does not look like a Telegram bot token (123456:ABC...)."
    if config["telegram_chat"] and not TELEGRAM_CHAT.fullmatch(config["telegram_chat"]):
        return "The Telegram chat id is a number (or @channelname)."
    save_json(SETTINGS, config)
    return None


def configured(config=None):
    config = config or settings()
    return bool(config["discord"] or (config["telegram_token"] and config["telegram_chat"]))


# -- sending ---------------------------------------------------------------------------
def _discord(url, text):
    response = requests.post(url, json={"content": text[:1900], "username": "Clip Studio",
                                        "allowed_mentions": {"parse": []}},
                             timeout=HTTP_TIMEOUT)
    if response.status_code not in (200, 204):
        raise ValueError("Discord said HTTP %s" % response.status_code)


def _telegram(token, chat, text, base="https://api.telegram.org"):
    response = requests.post("%s/bot%s/sendMessage" % (base, token),
                             json={"chat_id": chat, "text": text[:4000],
                                   "disable_web_page_preview": True}, timeout=HTTP_TIMEOUT)
    if response.status_code != 200:
        raise ValueError("Telegram said HTTP %s" % response.status_code)


def send(text, config=None):
    """Send to every configured app. Returns [problems] (empty when all went out)."""
    config = config or settings()
    problems = []
    if config["discord"]:
        try:
            _discord(config["discord"], text)
        except (requests.RequestException, ValueError) as error:
            problems.append("Discord: %s" % error)
    if config["telegram_token"] and config["telegram_chat"]:
        try:
            _telegram(config["telegram_token"], config["telegram_chat"], text)
        except (requests.RequestException, ValueError) as error:
            problems.append("Telegram: %s" % error)
    return problems


def notify(event, key, text, cooldown_hours=20):
    """Send once: nothing when the event is switched off, or `key` went out within
    the cooldown. Returns True when sent."""
    config = settings()
    if not config["events"].get(event) or not configured(config):
        return False
    with _sent_lock.hold(timeout=90):      # two recorders never send the same alert
        sent = load_json(SENT, {})
        sent = sent if isinstance(sent, dict) else {}
        now = time.time()
        if now - float(sent.get(key, 0)) < cooldown_hours * 3600:
            return False
        if send(text, config):
            return False                    # not sent: the next check tries again
        sent = {k: t for k, t in sent.items() if now - float(t) < 14 * 86400}
        sent[key] = now
        save_json(SENT, sent)
    return True


# -- the checks ------------------------------------------------------------------------
def check_spikes():
    from . import research
    config = settings()
    df = research.breakouts(["twitch", "kick"], min_viewers=config["spike_min_viewers"])
    for _i, row in (df.head(5).iterrows() if not df.empty else ()):
        if row["jump"] and row["jump"] >= config["spike_jump"]:
            notify("spike", "spike:%s" % row["key"],
                   "⚡ %s is spiking: %s viewers, about %.1fx its usual for this hour. "
                   "Clips from it could take off - check the Clip radar."
                   % (row["name"], "{:,}".format(int(row["viewers"])), row["jump"]))


def check_live(api):
    from . import watchlist
    follows = watchlist.entries()
    if not follows:
        return
    live = watchlist.live_now(api, [e["id"] for e in follows])
    for entry in follows:
        stream = live.get(entry["id"])
        if stream:
            notify("live", "live:%s:%s" % (entry["id"], stream.get("started_at")),
                   "🔴 %s is live: %s (%s viewers) - twitch.tv/%s"
                   % (entry["name"], stream.get("game_name") or "?",
                      "{:,}".format(stream.get("viewer_count") or 0), entry["login"]),
                   cooldown_hours=6)


def check_launches():
    from . import releases
    data = releases.load()
    for game in releases.this_week(data["games"], days=1) if data else []:
        days = releases.days_until(game)
        notify("launch", "launch:%s" % game.get("appid"),
               "🎮 %s launches %s (#%d most wishlisted on Steam). Launch week is when its "
               "clips get the most views. %s" % (game["name"], "today" if days == 0 else
                                                 "tomorrow", game.get("rank", 0),
                                                 game.get("store") or ""),
               cooldown_hours=72)


def autopilot_done(summary):
    from .autopilot import total
    problems = summary.get("errors") or []
    notify("autopilot", "autopilot:%s" % int(summary.get("started", time.time())),
           "🤖 Autopilot done: %d new clip(s) in %d min%s.\n%s"
           % (total(summary), summary.get("seconds", 0) // 60,
              ", %d problem(s)" % len(problems) if problems else "",
              summary.get("folder", "")))


def run_checks(api):
    """All the checks, after a stats snapshot. An alert must never break recording."""
    if not configured():
        return
    for check in (check_spikes, lambda: check_live(api), check_launches):
        try:
            check()
        except Exception as error:          # noqa: BLE001 - log and carry on
            save_json(DATA_DIR / "alerts_error.json",
                      {"at": datetime.now().isoformat(timespec="seconds"),
                       "error": str(error)[:300]})
