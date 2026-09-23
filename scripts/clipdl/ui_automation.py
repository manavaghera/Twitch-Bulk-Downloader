"""The Automation page: everything that runs by itself - the daily Autopilot,
recording with the page closed, phone alerts and disk cleanup."""

import streamlit as st

from . import alerts, cleanup, ui_autopilot, ui_login, ui_theme
from .util import human_size, load_json

PARTS = ["🤖 Autopilot", "🌙 Background recording", "🔔 Phone alerts", "🧹 Disk cleanup"]


def render(api):
    part = st.segmented_control("Automation", PARTS, default=PARTS[0], required=True,
                                key="auto_part", label_visibility="collapsed")
    if part == PARTS[0]:
        ui_autopilot.render(api)
    elif part == PARTS[1]:
        from .ui_research_more import background_box
        with st.container(border=True, key="card_auto_rec"):
            background_box(where="_auto")
    elif part == PARTS[2]:
        alerts_box()
    else:
        cleanup_box()


def _can_edit():
    return not ui_login.needs_login()


# -- phone alerts ----------------------------------------------------------------------
def _hidden(value):
    return "…" + value[-4:] if value else "not set"


def alerts_box():
    config = alerts.settings()
    with st.container(border=True, key="card_alerts"):
        ui_theme.step("🔔", "Phone alerts",
                      "A Discord or Telegram message when a game spikes, a streamer you "
                      "follow goes live, a big launch is near or the Autopilot is done.")
        ui_theme.note("Discord: <b>%s</b> &nbsp;·&nbsp; Telegram: <b>%s</b>" % (
            "connected (%s)" % _hidden(config["discord"]) if config["discord"] else "off",
            "connected" if config["telegram_token"] and config["telegram_chat"] else "off"))
        if not _can_edit():
            st.caption("🔒 Sign in to change the alerts.")
            return
        with st.expander("Connect Discord or Telegram", icon=":material/link:",
                         expanded=not alerts.configured(config)):
            st.markdown(
                "**Discord** - in your server: channel settings → *Integrations* → "
                "*Webhooks* → *New Webhook* → *Copy Webhook URL*.\n\n"
                "**Telegram** - message **@BotFather**, send `/newbot`, copy the token. Then "
                "send your new bot any message and open "
                "`https://api.telegram.org/bot<token>/getUpdates` - your chat id is the "
                "number after `\"chat\":{\"id\":`.")
            with st.form("alert_keys", border=False):
                discord = st.text_input("Discord webhook URL", type="password",
                                        placeholder="https://discord.com/api/webhooks/...")
                cols = st.columns(2)
                token = cols[0].text_input("Telegram bot token", type="password",
                                           placeholder="123456:ABC...")
                chat = cols[1].text_input("Telegram chat id", placeholder="123456789")
                saved = st.form_submit_button("Save", icon=":material/save:")
            if saved:
                changes = {k: v.strip() for k, v in (("discord", discord), (
                    "telegram_token", token), ("telegram_chat", chat)) if v.strip()}
                problem = alerts.save_settings(**changes) if changes else None
                (st.error(problem) if problem else st.toast("Saved."))
                if not problem:
                    st.rerun()
            cols = st.columns(2)
            if config["discord"] and cols[0].button("Disconnect Discord", width="stretch"):
                alerts.save_settings(discord="")
                st.rerun()
            if config["telegram_token"] and cols[1].button("Disconnect Telegram",
                                                           width="stretch"):
                alerts.save_settings(telegram_token="", telegram_chat="")
                st.rerun()

        st.markdown("**Tell me when**")
        events = {}
        cols = st.columns(2)
        for i, (name, label) in enumerate(alerts.EVENTS.items()):
            events[name] = cols[i % 2].checkbox(label, value=config["events"].get(name, True),
                                                key="al_ev_%s" % name)
        cols = st.columns(2)
        jump = cols[0].slider("Spike: at least this many times the usual viewers", 1.5, 5.0,
                              float(config["spike_jump"]), 0.5, key="al_jump")
        least = cols[1].select_slider("Spike: games with at least", [500, 1000, 2000, 5000,
                                                                     10000, 25000],
                                      value=int(config["spike_min_viewers"]), key="al_min",
                                      format_func="{:,} viewers".format)
        values = {"events": events, "spike_jump": jump, "spike_min_viewers": least}
        cols = st.columns(2)
        if cols[0].button("Save", icon=":material/save:", width="stretch", key="al_save",
                          disabled=all(config.get(k) == v for k, v in values.items())):
            alerts.save_settings(**values)
            st.toast("Saved.")
            st.rerun()
        if cols[1].button("Send a test message", icon=":material/send:", width="stretch",
                          disabled=not alerts.configured(config), key="al_test"):
            problems = alerts.send("✅ Clip Studio alerts work - you will hear from me here.",
                                   config)
            (st.error("; ".join(problems)) if problems else st.success("Sent - check your phone."))
        st.caption("Checked with every stats snapshot (every 15 min), so recording has to run "
                   "- this page open, or the background recorder. Each alert is sent once.")
        error = load_json(alerts.DATA_DIR / "alerts_error.json", {})
        if error:
            st.caption("Last problem (%s): %s" % (error.get("at"), error.get("error")))


# -- disk cleanup ----------------------------------------------------------------------
def cleanup_box():
    config = cleanup.settings()
    with st.container(border=True, key="card_cleanup"):
        ui_theme.step("🧹", "Disk cleanup",
                      "Old dated folders - Autopilot runs, clip radar and streamer downloads - "
                      "go to the Recycle Bin, so nothing is lost by mistake. Game folders "
                      "are never touched.")
        cols = st.columns([1.2, 1], vertical_alignment="bottom")
        days = cols[0].select_slider("Older than", [3, 7, 14, 30, 60, 90], value=config["days"]
                                     if config["days"] in (3, 7, 14, 30, 60, 90) else 14,
                                     format_func="{} days".format, key="cl_days")
        auto = cols[1].toggle("Clean up after each Autopilot run", value=config["auto"],
                              key="cl_auto", disabled=not _can_edit())
        if (days, auto) != (config["days"], config["auto"]) and _can_edit():
            cleanup.save_settings(days=days, auto=auto)
        old = cleanup.old_folders(days)
        if not old:
            ui_theme.note("Nothing older than %d days - the disk is tidy." % days)
            return
        total = sum(f["bytes"] for f in old)
        ui_theme.kpis([("Folders", str(len(old)), None, None),
                       ("Videos and files", "{:,}".format(sum(f["files"] for f in old)),
                        None, None),
                       ("Space", human_size(total), None, "freed once the Recycle Bin is emptied")])
        st.dataframe([{"Folder": str(f["path"]), "Age": "%d days" % f["age"],
                       "Files": f["files"], "Size": human_size(f["bytes"])} for f in old],
                     hide_index=True, width="stretch")
        if ui_login.may_download("cl_sign_in") and st.button(
                "Move %d folder%s (%s) to the Recycle Bin" % (
                    len(old), "" if len(old) == 1 else "s", human_size(total)),
                type="primary", icon=":material/delete:", width="stretch"):
            moved, freed, problems = cleanup.to_recycle_bin(old)
            st.success("Moved %d folder(s) to the Recycle Bin (%s)." % (moved, human_size(freed)))
            for problem in problems:
                st.warning(problem)
        st.caption("The download history keeps these clips, so they are not downloaded again.")
