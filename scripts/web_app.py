"""
Clip Studio  --  the web page for both tools
==============================================================================

Run it locally (double-click start_web_ui.bat, or):

    streamlit run scripts\\web_app.py

It opens at http://localhost:8501. Everything still happens on this PC: clips
land in downloads\\ and credentials are read from data\\config.json, exactly as
for the two scripts, which keep working on their own.

To host it later, see the notes at the top of scripts\\clipdl\\ui_common.py:
the credentials move into environment variables or Streamlit secrets, and an
APP_PASSWORD keeps strangers from running downloads on your server.
"""

import sys
from pathlib import Path

# Streamlit runs this file directly, so put scripts/ on the import path the
# same way the two launchers do.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Clip Studio", page_icon="🎮", layout="wide",
                   initial_sidebar_state="expanded")

from clipdl import (jobs, ui_automation, ui_channel, ui_common, ui_downloader,  # noqa: E402
                    ui_login, ui_radar, ui_releases, ui_research, ui_streamers, ui_studio,
                    ui_theme, ui_trends, ui_youtube)
from clipdl import releases  # noqa: E402
from clipdl.trends_cli import youtube_key  # noqa: E402

ui_theme.apply()
ui_common.load_hosted_secrets()
ui_login.gate()

api = ui_common.sidebar_connection(account_box=ui_login.sidebar_box,
                                   can_edit=not ui_login.needs_login())

chips = [("Twitch connected", "ok") if api else ("Twitch not connected", "bad"),
         ("YouTube signals on", "ok") if youtube_key() else ("YouTube signals off", "")]
busy = [job.label for job in (jobs.running(kind) for kind in (
    "download", "trends", "studio", "upload", "youtube")) if job]
chips.append(("Running: %s" % " + ".join(busy), "live") if busy else ("Ready", "ok"))
launches = releases.load()
soon = releases.this_week(launches["games"]) if launches else []
if soon:
    chips.append(("%d game launch%s this week - see Releases" % (
        len(soon), "" if len(soon) == 1 else "es"), "live"))
ui_theme.hero("🎮 Clip <span>Studio</span>",
              "Find the games blowing up in the US and Europe this week, then pull their "
              "best Twitch clips as ready-to-edit videos or vertical Shorts.", chips)

tabs = st.tabs(["📈  Trend research", "🔬  Game research", "📡  Clip radar", "👀  Streamers",
                "📅  Releases", "⬇️  Clip downloader", "▶️  YouTube", "🎬  Studio",
                "📺  My channel", "🤖  Automation"], key="main_tab", on_change="rerun")
for tab, page in zip(tabs, (ui_trends, ui_research, ui_radar, ui_streamers, ui_releases,
                            ui_downloader, ui_youtube, ui_studio, ui_channel,
                            ui_automation)):
    # Every tab is drawn so its settings are kept while you look elsewhere, but
    # the slow parts (clip scans, live checks) only run in the open one.
    st.session_state["_tab_open"] = tab.open is not False
    with tab:
        page.render(api)
