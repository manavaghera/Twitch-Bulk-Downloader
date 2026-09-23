"""The Releases page: a calendar of upcoming launches from Steam wishlists."""

import time
from datetime import date

import streamlit as st

from . import jobs, releases, ui_theme, wishlist
from .ui_common import progress_panel, start_job

VERDICT_CHIPS = {"boom": ("🔥 Boom likely", "cs-warn"), "heating": ("🚀 Heating up", "cs-good"),
                 "strong": ("📈 Strong", "cs-info"), "cooling": ("🧊 Losing steam", "cs-move"),
                 "watch": ("👀 Watch", "cs-move")}
CSS = """<style>
.cs-cal { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px; }
.cs-cal .dow { color: #8C8C99; font-size: .75rem; font-weight: 700; text-align: center; }
.cs-cal .day { min-height: 92px; border-radius: 10px; background: #16161B;
  border: 1px solid #24242b; padding: 6px; font-size: .74rem; overflow: hidden; }
.cs-cal .day.empty { background: transparent; border-color: transparent; }
.cs-cal .day.today { border-color: #9146FF; box-shadow: inset 0 0 0 1px #9146FF; }
.cs-cal .day.past { opacity: .45; }
.cs-cal .n { font-weight: 800; color: #ADADB8; margin-bottom: 4px; }
.cs-cal .g { display: block; margin-top: 4px; color: #EFEFF1; text-decoration: none; }
.cs-cal .g img { width: 100%; aspect-ratio: 460 / 215; object-fit: cover; border-radius: 5px;
  display: block; }
.cs-cal .g span { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  margin-top: 2px; font-weight: 600; }
.cs-cal .more { color: #bf94ff; font-weight: 700; margin-top: 3px; }
@media (max-width: 640px) { .cs-cal .g img { display: none; } .cs-cal .day { min-height: 60px; } }
</style>"""


def _countdown(days):
    return "today" if days == 0 else "tomorrow" if days == 1 else "in %d days" % days


def render(api):
    data = releases.load()
    job = jobs.latest("trends")
    busy = bool(job and job.running)
    with st.container(border=True, key="card_releases"):
        ui_theme.step("📅", "Release calendar",
                      "Upcoming launches from Steam's most-wishlisted chart. Launch week is when "
                      "a new game's clips get the most views - be ready before it drops.")
        cols = st.columns([3, 1], vertical_alignment="bottom")
        if data:
            cols[0].caption("From the wishlist scan of %s. Trend research refreshes it too."
                            % time.strftime("%d %b %H:%M", time.localtime(data["scanned_at"])))
        if cols[1].button("Scan now", icon=":material/refresh:", width="stretch",
                          disabled=busy or api is None):
            start_job("trends", "Steam wishlist scan", lambda: wishlist.scan(api))
    if job and job.running and job.label == "Steam wishlist scan":
        progress_panel("trends", where="_rel")
        return
    if not data:
        ui_theme.empty_state("📅", "No release data yet",
                             "Press Scan now (about a minute), or run Trend research with Steam "
                             "wishlists on.")
        return
    games = data["games"]
    soon = releases.this_week(games)
    ui_theme.insights([
        ("🚀", "<b>%d</b> wishlisted game%s launch%s in the next 7 days%s." % (
            len(soon), "" if len(soon) == 1 else "s", "es" if len(soon) == 1 else "",
            ": " + ", ".join("<b>%s</b> (%s)" % (ui_theme.escape(g["name"]),
                                               _countdown(releases.days_until(g)))
                             for g in soon[:4]) if soon else "")),
        ("📆", "<b>%d</b> have an exact date, %d only a month, quarter or year, %d none yet."
               % (len(releases.dated(games)),
                  sum(1 for g in games if g.get("precision") in ("month", "quarter", "year")),
                  sum(1 for g in games if g.get("precision") in ("tba", "soon"))))],
        title="Coming up")

    calendar_view(games)
    st.markdown("#### Countdown")
    window = st.segmented_control("Launching within", (30, 90, 365), default=90, required=True,
                                  format_func="{} days".format, key="rel_window")
    upcoming = [g for g in releases.dated(games) if releases.days_until(g) <= window]
    if upcoming:
        ui_theme.ranked([{
            "pos": "", "icon": g.get("art"), "name": g["name"], "url": g.get("store"),
            "sub": "%s · #%d most wishlisted · %s" % (releases.when_text(g), g["rank"],
                                                      ", ".join(g.get("tags") or [])),
            "chips": [VERDICT_CHIPS.get(g.get("verdict"), ("", "cs-move")),
                      ("hype %d" % g.get("hype", 0), "cs-info")],
            "value": "today" if not releases.days_until(g) else "%dd" % releases.days_until(g),
            "value_sub": "to launch" if releases.days_until(g) else "launch day"}
            for g in upcoming], wide=True)
    else:
        st.info("Nothing with an exact date in that window.")
    with st.expander("No exact day yet (%d)" % sum(len(v) for v in releases.undated(
            games).values()), icon=":material/event_busy:"):
        for label, group in sorted(releases.undated(games).items()):
            st.markdown("**%s**" % label)
            ui_theme.mini_list([(g.get("art") or "", g["name"], "#%d" % g["rank"])
                                for g in sorted(group, key=lambda g: g["rank"])])
    reminders(games)


def calendar_view(games):
    today = date.today()
    months = ["%04d-%02d" % (today.year + (today.month - 1 + i) // 12,
                             (today.month - 1 + i) % 12 + 1) for i in range(3)]
    pick = st.segmented_control("Month", months, default=months[0], required=True,
                                key="rel_month", format_func=lambda ym: date(
                                    int(ym[:4]), int(ym[5:]), 1).strftime("%B %Y"))
    year, month_number = int(pick[:4]), int(pick[5:])
    cells = ['<div class="dow">%s</div>' % d for d in ("Mon", "Tue", "Wed", "Thu", "Fri",
                                                       "Sat", "Sun")]
    for week in releases.month(year, month_number, games):
        for day, launches in week:
            if not day:
                cells.append('<div class="day empty"></div>')
                continue
            this = date(year, month_number, day)
            kind = " today" if this == today else " past" if this < today else ""
            items = "".join(
                '<a class="g" href="%s" target="_blank" rel="noopener">%s<span>%s</span></a>'
                % (ui_theme.escape(g.get("store") or "#", quote=True),
                   '<img src="%s" alt="">' % ui_theme.escape(g["art"], quote=True)
                   if g.get("art") else "", ui_theme.escape(g["name"]))
                for g in launches[:2])
            more = ('<div class="more">+%d more</div>' % (len(launches) - 2)
                    if len(launches) > 2 else "")
            cells.append('<div class="day%s"><div class="n">%d</div>%s%s</div>'
                         % (kind, day, items, more))
    ui_theme.html(CSS + '<div class="cs-cal">%s</div>' % "".join(cells))


def reminders(games):
    with st.container(border=True, key="card_reminders"):
        ui_theme.step("🔔", "Reminders on your phone",
                      "Download a calendar file and open it: Google Calendar, Outlook and the "
                      "iPhone calendar all add the launches, with an alert before each one.")
        dated = releases.dated(games)
        names = {g["appid"]: g["name"] for g in dated}
        default = [g["appid"] for g in sorted(dated, key=lambda g: g["rank"])[:10]]
        cols = st.columns([3, 1], vertical_alignment="bottom")
        picked = cols[0].multiselect("Remind me about", list(names), default=default,
                                     format_func=names.get, key="rel_pick")
        days = cols[1].select_slider("Days before", [1, 2, 3, 5, 7], value=3, key="rel_days")
        chosen = [g for g in dated if g["appid"] in picked]
        st.download_button("Download reminders (.ics) - %d launch%s" % (
            len(chosen), "" if len(chosen) == 1 else "es"), releases.ics(chosen, days),
            file_name="game-launches.ics", mime="text/calendar", icon=":material/event:",
            disabled=not chosen, width="stretch")
