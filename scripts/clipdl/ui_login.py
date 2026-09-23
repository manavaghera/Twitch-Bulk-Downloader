"""Signing in to the web page. Accounts are made by the owner only - see accounts.py."""

import os

import streamlit as st

from . import accounts, ui_theme
from .ui_common import is_hosted, password_gate

NO_SIGNUP = "No account? Ask the owner of this page - there is no sign-up."


def _public_on_purpose():
    return os.environ.get("CLIPDL_PUBLIC", "").strip() not in ("", "0", "false")


def _hosted_users():
    """The optional [users] table from Streamlit secrets, when hosted."""
    try:
        table = st.secrets.get("users")
    except Exception:           # no secrets file at all - the usual local case
        return {}
    return dict(table) if table else {}


def accounts_exist():
    return bool(accounts.all_users(_hosted_users()))


def current_user():
    return st.session_state.get("user")


def needs_login():
    """True when accounts exist and this browser has not signed in."""
    return current_user() is None and accounts_exist()


def _form(key):
    with st.form(key, border=False):
        login_id = st.text_input("ID", icon=":material/person:", autocomplete="username")
        password = st.text_input("Password", type="password", icon=":material/lock:",
                                 autocomplete="current-password")
        go = st.form_submit_button("Sign in", type="primary", icon=":material/login:",
                                   width="stretch")
    if not go:
        return
    user, problem = accounts.check(login_id, password, _hosted_users())
    if user:
        st.session_state["user"] = user
        st.rerun()
    st.error(problem, icon=":material/lock:")


def gate():
    """Call at the top of the page: stops it until a sign-in, when that is required."""
    if not accounts_exist():
        if is_hosted() and not os.environ.get("APP_PASSWORD") and not _public_on_purpose():
            # Safe by default: a public server with no sign-in at all would let
            # anyone on the internet run downloads on it.
            st.error("This hosted page has no sign-in set up, so it stays locked. Add "
                     "accounts (a [users] table in the secrets - see clipdl/accounts.py), "
                     "or set CLIPDL_PUBLIC=1 to open it to everyone on purpose.",
                     icon=":material/lock:")
            st.stop()
        password_gate()         # the older single APP_PASSWORD, if one is set
        return
    if accounts.login_scope() != "site" or current_user():
        return
    _, middle, _ = st.columns([1, 1.4, 1])
    with middle:
        st.space("large")
        ui_theme.hero("🎮 Clip <span>Studio</span>", "This page is private. Sign in with the "
                      "ID and password you were given.", [])
        with st.container(border=True, key="card_login"):
            _form("login_site")
        st.caption(NO_SIGNUP)
    st.stop()


@st.dialog("Sign in to download", icon=":material/lock:")
def download_dialog():
    st.caption("Downloading clips is for approved accounts only. " + NO_SIGNUP)
    _form("login_download")


def sidebar_box():
    """Who is signed in, with a sign-out button - or how to sign in."""
    if not accounts_exist():
        return
    user = current_user()
    if user:
        cols = st.columns([3, 2], vertical_alignment="center")
        cols[0].markdown(":material/account_circle: **%s**" % user)
        if cols[1].button("Sign out", key="sign_out", width="stretch"):
            st.session_state.pop("user", None)
            st.rerun()
    elif st.button("Sign in", key="sign_in_side", icon=":material/login:", width="stretch"):
        download_dialog()


def may_download(key):
    """True when this browser may download. When a sign-in is needed first, shows
    a sign-in button in the download button's place and returns False."""
    if not needs_login():
        return True
    if st.button("Sign in to download", key=key, icon=":material/lock:", type="primary",
                 width="stretch"):
        download_dialog()
    return False
