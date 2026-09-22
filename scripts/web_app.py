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

from clipdl import jobs, ui_common, ui_downloader, ui_login, ui_theme, ui_trends  # noqa: E402
from clipdl.trends_cli import youtube_key  # noqa: E402

ui_theme.apply()
ui_common.load_hosted_secrets()
ui_login.gate()

api = ui_common.sidebar_connection(account_box=ui_login.sidebar_box,
                                   can_edit=not ui_login.needs_login())

chips = [("Twitch connected", "ok") if api else ("Twitch not connected", "bad"),
         ("YouTube signals on", "ok") if youtube_key() else ("YouTube signals off", "")]
running = jobs.running()
chips.append(("Running: %s" % running.label, "live") if running else ("Ready", "ok"))
ui_theme.hero("🎮 Clip <span>Studio</span>",
              "Find the games blowing up in the US and Europe this week, then pull their "
              "best Twitch clips as ready-to-edit videos or vertical Shorts.", chips)

trends_tab, download_tab = st.tabs(["📈  Trend research", "⬇️  Clip downloader"],
                                   key="main_tab", on_change="rerun")
with trends_tab:
    ui_trends.render(api)
with download_tab:
    ui_downloader.render(api)
