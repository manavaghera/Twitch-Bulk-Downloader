"""Pieces both web pages share: sign-in, the Twitch client, the progress panel.

LOCAL vs HOSTED
Locally, credentials come from data/config.json exactly as for the scripts,
and the page can save them there. On a server, set them as environment
variables or Streamlit secrets instead:

    TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET   required
    YOUTUBE_API_KEY                          optional
    APP_PASSWORD                             one shared password, used only
                                             when no accounts exist - accounts
                                             (see accounts.py) are better
    CLIPDL_LOGIN                             "site" or "downloads": what a
                                             sign-in protects (accounts.py)
    [users] table                            hosted accounts (accounts.py)
    CLIPDL_HOSTED=1                          hides the local-only buttons, and
                                             keeps the page locked until a
                                             sign-in exists
    CLIPDL_PUBLIC=1                          hosted with no sign-in, on purpose
"""

import os
from html import escape

import requests
import streamlit as st

from . import jobs, locks, timing, ui_theme
from .api import TwitchAPI, TwitchError
from .cli import stored_credentials
from .config import CONFIG_FILE, DATA_DIR
from .util import load_json, save_json

SECRET_KEYS = ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "YOUTUBE_API_KEY",
               "APP_PASSWORD", "CLIPDL_HOSTED", "CLIPDL_LOGIN", "CLIPDL_PUBLIC")


def load_hosted_secrets():
    """Copy Streamlit secrets into the environment, where the rest looks."""
    try:
        secrets = dict(st.secrets)
    except Exception:           # no secrets file at all - the usual local case
        return
    for key in SECRET_KEYS:
        if key in secrets and not os.environ.get(key):
            os.environ[key] = str(secrets[key])


def is_hosted():
    return os.environ.get("CLIPDL_HOSTED", "").strip() not in ("", "0", "false")


def password_gate():
    """Stop the page until APP_PASSWORD is entered, if one is set."""
    wanted = os.environ.get("APP_PASSWORD", "")
    if not wanted or st.session_state.get("unlocked"):
        return
    _, middle, _ = st.columns([1, 1.4, 1])
    with middle:
        st.space("large")
        ui_theme.hero("🎮 Clip <span>Studio</span>", "This page is private. Enter the password "
                      "to continue.", [])
        typed = st.text_input("Password", type="password", icon=":material/lock:")
        if typed and typed == wanted:
            st.session_state["unlocked"] = True
            st.rerun()
        elif typed:
            st.error("Wrong password.")
    st.stop()


@st.cache_resource(show_spinner=False)
def twitch_client(client_id, client_secret):
    """One TwitchAPI per set of credentials, shared across reruns and tabs."""
    api = TwitchAPI(client_id, client_secret)
    api.token()                 # raises TwitchError on bad credentials
    return api


@st.cache_data(ttl=3600, show_spinner=False)
def _box_arts(client_id, client_secret, ids):
    api = twitch_client(client_id, client_secret)
    data = api.get("/games", {"id": list(ids)}).get("data") or []
    return {game["id"]: game.get("box_art_url") or "" for game in data}


def box_arts(api, ids):
    """{Twitch game id: box art url template}, cached for an hour. {} on any trouble."""
    ids = tuple(sorted({str(i) for i in ids if i}))
    if api is None or not ids:
        return {}
    found = {}
    for start in range(0, len(ids), 100):          # Twitch takes 100 ids per request
        try:
            found.update(_box_arts(api.client_id, api.client_secret, ids[start:start + 100]))
        except TwitchError:
            pass
    return found


def _side_status(dot, title, sub):
    ui_theme.html('<div class="cs-side-status"><span class="cs-dot %s"></span>'
                  '<div><b>%s</b><div class="sub">%s</div></div></div>' % (dot, title, sub))


def _sidebar_footer():
    st.divider()
    with st.expander("How it works", icon=":material/help:"):
        st.markdown(
            "1. **Trend research** scans Steam, Wikipedia, Twitch, Kick, IGDB and "
            "(optionally) YouTube to see which games are big or climbing this week.\n"
            "2. **Clip downloader** grabs the best English gameplay clips of a game - "
            "picks from your research show up first.\n\n"
            "A download and a trend scan can run at the same time, each with its own "
            "progress, countdown and Cancel button.")


def sidebar_connection(account_box=None, can_edit=True):
    """Show the Twitch connection in the sidebar. Returns a TwitchAPI or None.

    `account_box`, if given, draws the signed-in account under the connection.
    `can_edit` False hides the credentials form from visitors not signed in.
    """
    with st.sidebar:
        ui_theme.html('<div class="cs-brand"><div class="logo">🎮</div><div>'
                      '<div class="w">Clip Studio</div>'
                      '<div class="s">Twitch trends &amp; clips</div></div></div>')
        api = _connection(can_edit)
        _youtube(can_edit)
        if account_box:
            st.divider()
            account_box()
        _sidebar_footer()
    return api


def _connection(can_edit=True):
    found = stored_credentials()
    if found:
        try:
            api = twitch_client(found[0], found[1])
        except TwitchError as error:
            _side_status("bad", "Twitch connection failed", "Check the credentials below")
            st.error(str(error))
        else:
            _side_status("ok", "Connected to Twitch",
                         "App %s…" % escape(found[0][:6]))
            with st.expander("Connection details", icon=":material/key:"):
                st.caption("Credentials from %s" % found[2])
            return api
    else:
        _side_status("bad", "Not connected", "Add your Twitch app keys")
    if not can_edit:
        st.caption("Sign in to set up the Twitch connection.")
        return None

    st.caption("Paste your Twitch app credentials - free at "
               "[dev.twitch.tv/console](https://dev.twitch.tv/console).")
    with st.form("credentials", border=False):
        client_id = st.text_input("Client ID").strip()
        client_secret = st.text_input("Client Secret", type="password").strip()
        save = False
        if not is_hosted():
            save = st.checkbox("Remember on this PC", value=True,
                               help="Saved to data/config.json")
        submitted = st.form_submit_button("Connect", type="primary", width="stretch")
    if not submitted:
        return None
    if not client_id or not client_secret:
        st.error("Both fields are needed.")
        return None
    try:
        api = twitch_client(client_id, client_secret)
    except TwitchError as error:
        st.error(str(error))
        return None
    if save:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        config = load_json(CONFIG_FILE, {})
        config = config if isinstance(config, dict) else {}
        config.update({"client_id": client_id, "client_secret": client_secret})
        save_json(CONFIG_FILE, config)
    else:
        # Kept for this server process only, never written to disk.
        os.environ["TWITCH_CLIENT_ID"] = client_id
        os.environ["TWITCH_CLIENT_SECRET"] = client_secret
    st.rerun()


YOUTUBE_GUIDE = "https://console.cloud.google.com/apis/library/youtube.googleapis.com"


def check_youtube_key(key):
    """(True, "") if the key works, else (False, what to do about it). Costs 1 of the
    10,000 free daily quota units."""
    from . import yt_quota
    yt_quota.spend(1, "videos")
    try:
        response = requests.get("https://www.googleapis.com/youtube/v3/videos", params={
            "part": "id", "chart": "mostPopular", "maxResults": 1, "regionCode": "US",
            "key": key}, timeout=15)
    except requests.RequestException as error:
        return False, "Could not reach YouTube (%s)." % error
    if response.status_code == 200:
        return True, ""
    try:
        error = response.json().get("error") or {}
    except ValueError:
        error = {}
    reason = ((error.get("errors") or [{}])[0].get("reason") or "")
    detail = " ".join(str(d.get("reason", "")) for d in error.get("details") or [])
    if reason == "keyInvalid" or "API_KEY_INVALID" in detail:
        return False, "That key is not valid - copy it again from Google Cloud."
    if reason in ("accessNotConfigured", "forbidden") or "SERVICE_DISABLED" in detail:
        return False, ("YouTube Data API v3 is not turned on for this key's project. "
                       "Open the API library, pick it, press Enable, wait a minute, retry.")
    if "quota" in reason.lower():
        yt_quota.exhausted()
        return False, "This key's daily quota is used up - it resets at midnight Pacific time."
    if "Blocked" in reason or "blocked" in (error.get("message") or ""):
        return False, ("The key's restrictions block this app. Under the key's settings, set "
                       "Application restrictions to None, and API restrictions to YouTube "
                       "Data API v3.")
    return False, "YouTube said: %s" % (error.get("message") or response.status_code)


def _youtube(can_edit):
    """The optional YouTube key: its status, and a box to add, test and remove it."""
    from .trends_cli import youtube_key
    key = youtube_key()
    from_env = bool(os.environ.get("YOUTUBE_API_KEY", "").strip())
    with st.expander("YouTube: %s" % ("on" if key else "off (optional)"),
                     icon=":material/smart_display:"):
        if key:
            st.caption("✅ Key …%s %s. YouTube is part of trend research and Game research."
                       % (escape(key[-4:]), "from YOUTUBE_API_KEY" if from_env else "saved"))
            from . import yt_quota
            used = yt_quota.used_today()
            st.progress(min(used / yt_quota.DAILY, 1.0),
                        text="Today: %s of %s free units used" % (
                            "{:,}".format(used), "{:,}".format(yt_quota.DAILY)))
            st.caption("The stats recorder pauses with %s left, so Shorts checks and "
                       "uploads still work. Resets at midnight Pacific."
                       % "{:,}".format(yt_quota.RESERVE))
        else:
            st.caption("Adds YouTube's trending gaming videos and live gaming streams to the "
                       "research. The key is free - [get one here](%s): create a project, "
                       "enable **YouTube Data API v3**, then Credentials → Create API key."
                       % YOUTUBE_GUIDE)
        if not can_edit or from_env or is_hosted():
            return
        with st.form("youtube_key", border=False):
            new = st.text_input("YouTube API key", type="password",
                                placeholder="AIza…").strip()
            go = st.form_submit_button("Save & test" if not key else "Replace & test",
                                       width="stretch")
        if go and new:
            ok, problem = check_youtube_key(new)
            if not ok:
                st.error(problem)
                return
            _save_config(youtube_api_key=new)
            st.rerun()
        if key and st.button("Remove the key", key="yt_remove", width="stretch"):
            _save_config(youtube_api_key=None)
            st.rerun()


def _save_config(**changes):
    """Merge into data/config.json; None removes a setting."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    config = load_json(CONFIG_FILE, {})
    config = config if isinstance(config, dict) else {}
    for name, value in changes.items():
        if value is None:
            config.pop(name, None)
        else:
            config[name] = value
    save_json(CONFIG_FILE, config)


def start_job(kind, label, work, estimate=None):
    """Start a background run, or explain why not. Returns True if it started.
    `estimate`: seconds it usually takes (timing.py), for the countdown."""
    try:
        jobs.start(kind, label, work, estimate)
    except jobs.Busy as error:
        st.warning(str(error))
        return False
    st.rerun()


def busy_elsewhere(kind):
    """Who else is downloading (another program: the scheduled Autopilot, the
    command line), or None. Trend scans run beside anything, so never wait."""
    if kind != "download" or jobs.running("download") or not locks.DOWNLOADS.busy_elsewhere():
        return None
    who = locks.owner()
    return who[0] if who else "download in another window"


def _minutes(seconds):
    return "%d:%02d" % divmod(int(seconds), 60)


def time_left(job):
    """"~1:20 left" for the progress panel, or "" before anything can be said."""
    step, whole = job.eta()
    if whole is not None:
        if whole <= 0:
            return "taking longer than usual"
        return "~%s left" % timing.clock(whole)
    if step is not None:
        return "~%s left in this step" % timing.clock(step) if step > 0 else "finishing"
    return ""


def tab_open():
    """False while the page draws a tab nobody is looking at (web_app sets it):
    slow network work (scans, live checks) waits until the tab is opened."""
    return st.session_state.get("_tab_open", True)


def usual_time(seconds):
    """The line under a start button: how long this usually takes."""
    st.caption("⏱ Usually takes %s on this PC." % timing.text(seconds))


@st.fragment(run_every=1.0)
def progress_panel(kind, can_cancel=True, where=""):
    """Live status of the running job. Redraws itself every second until done."""
    job = jobs.latest(kind)
    if job is None:
        return
    if not job.running:
        # Finished since the last redraw: rerun the whole page once so the
        # results appear, then this panel goes quiet.
        if st.session_state.get("shown_%s" % kind) != job.started:
            st.session_state["shown_%s" % kind] = job.started
            st.rerun(scope="app")
        return

    stopping = job.stop.is_set()
    # `where` keeps the widget keys apart when several tabs show the same job.
    with st.container(border=True, key="card_progress_%s%s" % (kind, where)):
        top = st.columns([5, 1], vertical_alignment="center")
        left = "" if stopping else time_left(job)
        with top[0]:
            ui_theme.html(
                '<div class="cs-step"><span class="cs-dot live" style="flex:none"></span>'
                '<div><div class="t">%s</div><div class="h">%s &nbsp;·&nbsp; %s elapsed%s</div>'
                '</div></div>' % (escape(job.label),
                                  "Stopping after the clips in flight" if stopping
                                  else "Running", _minutes(job.elapsed),
                                  " &nbsp;·&nbsp; <b>⏱ %s</b>" % escape(left) if left else ""))
        if top[1].button("Stopping…" if stopping else "Cancel", key="cancel_%s%s" % (kind, where),
                         icon=":material/stop_circle:", width="stretch",
                         disabled=stopping or not can_cancel):
            job.cancel()
        done = job.progress()
        if done and done[1]:
            st.progress(min(done[0] / float(done[1]), 1.0),
                        text="%d of %d done" % done)
        latest = next((line.strip() for line in reversed(job.text(last=6).splitlines())
                       if line.strip()), "Starting…")
        ui_theme.note("<b>Now:</b> %s" % escape(latest))
        with st.expander("Live log", icon=":material/terminal:"):
            st.code(job.text(last=40) or "Starting...", language=None)


def finished_log(job):
    """The whole log of a finished job, folded away."""
    status = ("cancelled" if job.cancelled else "failed" if job.error else "finished")
    with st.expander("Full log (%s in %s)" % (status, _minutes(job.elapsed))):
        st.code(job.text() or "(nothing was printed)", language=None)
