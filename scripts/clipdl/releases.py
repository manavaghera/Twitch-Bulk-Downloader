"""The release calendar: upcoming launches from Steam's most-wishlisted chart.

Every wishlist scan (trend research, or the Releases page's own button) saves
its games to data/releases.json, so the calendar works without scanning
again. Launch week is when clips of a new game get the most views, so the
page counts down to each date and can hand your calendar app an .ics file
with a reminder a few days before.
"""

import calendar
import time
from datetime import date, datetime, timedelta, timezone

from .config import DATA_DIR
from .util import load_json, save_json

FILE = DATA_DIR / "releases.json"


def save_scan(games):
    """Keep the latest wishlist scan: one dict per game."""
    rows = []
    for g in games:
        rows.append({"appid": g.appid, "name": g.name, "rank": g.rank,
                     "release": g.release.timestamp() if g.release else None,
                     "precision": g.precision, "art": g.art, "store": g.store_url,
                     "hype": g.score(7), "verdict": g.verdict(7), "tags": g.tags[:3],
                     "publisher": g.publisher, "hypes": g.hypes})
    save_json(FILE, {"scanned_at": time.time(), "games": rows})


def load():
    data = load_json(FILE, {})
    return data if isinstance(data, dict) and data.get("games") else None


def release_date(game):
    if not game.get("release"):
        return None
    return datetime.fromtimestamp(game["release"], timezone.utc).date()


def days_until(game):
    when = release_date(game)
    if when is None or game.get("precision") != "full":
        return None
    return (when - date.today()).days


def when_text(game):
    when = release_date(game)
    precision = game.get("precision")
    if when is None:
        return "Coming soon" if precision == "soon" else "Date not announced"
    if precision == "full":
        return when.strftime("%a %d %b %Y")
    if precision == "month":
        return when.strftime("%B %Y")
    if precision == "quarter":
        return "Q%d %d" % ((when.month - 1) // 3 + 1, when.year)
    return str(when.year)


def dated(games):
    """Games with an exact day, soonest first (ones already out are left out)."""
    rows = [g for g in games if g.get("precision") == "full" and days_until(g) is not None
            and days_until(g) >= 0]
    return sorted(rows, key=days_until)


def undated(games):
    """{"This month / quarter / year" label: [games]} for games without an exact day."""
    groups = {}
    for g in games:
        if g.get("precision") == "full":
            continue
        groups.setdefault(when_text(g), []).append(g)
    return groups


def month(year, month_number, games):
    """Weeks of the month: [[(day or 0, [games that day])] x 7] from Monday."""
    by_day = {}
    for g in dated(games):
        when = release_date(g)
        if (when.year, when.month) == (year, month_number):
            by_day.setdefault(when.day, []).append(g)
    return [[(day, by_day.get(day, [])) for day in week]
            for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month_number)]


def _ics_text(text):
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics(games, remind_days=3):
    """An .ics calendar: one all-day event per launch, with a reminder beforehand."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Clip Studio//Release calendar//EN",
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:Game launches (Clip Studio)"]
    for g in dated(games):
        day = release_date(g)
        lines += [
            "BEGIN:VEVENT", "UID:steam-%s@clipstudio" % g["appid"], "DTSTAMP:" + stamp,
            "DTSTART;VALUE=DATE:" + day.strftime("%Y%m%d"),
            "DTEND;VALUE=DATE:" + (day + timedelta(days=1)).strftime("%Y%m%d"),
            "SUMMARY:" + _ics_text("🎮 %s launches" % g["name"]),
            "DESCRIPTION:" + _ics_text("#%d most wishlisted on Steam. Launch week is when its "
                                       "clips get the most views - line up streamers and "
                                       "clips early.\n%s" % (g["rank"], g.get("store") or "")),
            "URL:" + (g.get("store") or ""),
            "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-P%dD" % remind_days,
            "DESCRIPTION:" + _ics_text("%s launches in %d days" % (g["name"], remind_days)),
            "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def this_week(games, days=7):
    """Launches in the next `days` days, soonest first."""
    return [g for g in dated(games) if days_until(g) <= days]
