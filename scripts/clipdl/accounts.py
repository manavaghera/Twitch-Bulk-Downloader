"""Who may use the web page: a fixed list of accounts, made by the owner only.

There is no sign-up. Accounts are added from the command line on the PC that
runs the page (scripts\\manage_users.py) and kept in data/users.json - which
holds salted PBKDF2 hashes, never the passwords themselves.

When hosted, accounts can come from Streamlit secrets instead, as a table of
login ID -> hash made with `manage_users.py hash`:

    [users]
    alice = "pbkdf2_sha256$600000$...$..."

WHERE THE LOGIN APPLIES
    "site"       (default) nothing on the page shows until you sign in
    "downloads"  anyone can look at the trend research, but downloading
                 clips (and the finished downloads) needs a sign-in
Set it with the CLIPDL_LOGIN environment variable / secret, or as
"login_required_for" in data/config.json. With no accounts at all the page
stays open, exactly as before.
"""

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import datetime, timezone

from .config import CONFIG_FILE, DATA_DIR
from .util import load_json, save_json

USERS_FILE = DATA_DIR / "users.json"
ITERATIONS = 600_000            # OWASP's current advice for PBKDF2-SHA256
MIN_PASSWORD = 8
SCOPES = ("site", "downloads")

# Guessing protection: this many wrong passwords for one ID within the window
# locks that ID for LOCK_SECONDS. Kept in memory for the life of the server.
MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60
LOCK_SECONDS = 5 * 60
MAX_TRACKED_IDS = 5000
_failures = {}                  # login id -> [timestamps of recent failures]
_failures_lock = threading.Lock()


def normalize(login_id):
    return (login_id or "").strip().lower()


def hash_password(password, iterations=ITERATIONS):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, base64.b64encode(salt).decode(),
                                       base64.b64encode(digest).decode())


def _matches(stored, password):
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     base64.b64decode(salt), int(iterations))
    except (ValueError, TypeError, AttributeError):
        return False
    return hmac.compare_digest(actual, expected)


# A real hash to check against when the ID does not exist, so a wrong ID takes
# as long as a wrong password and the timing gives nothing away.
_DUMMY = hash_password(secrets.token_hex(8))


# -- the account list ---------------------------------------------------------
def _file_users():
    data = load_json(USERS_FILE, {})
    users = data.get("users") if isinstance(data, dict) else None
    return users if isinstance(users, dict) else {}


def all_users(extra=None):
    """{login id: stored hash} from users.json plus any hosted `extra` table."""
    users = {normalize(k): (v.get("hash") if isinstance(v, dict) else v)
             for k, v in _file_users().items()}
    for login_id, stored in (extra or {}).items():
        users[normalize(login_id)] = str(stored)
    return {k: v for k, v in users.items() if k and v}


def login_scope():
    wanted = os.environ.get("CLIPDL_LOGIN", "").strip().lower()
    if not wanted:
        config = load_json(CONFIG_FILE, {})
        wanted = str((config or {}).get("login_required_for") or "").strip().lower() \
            if isinstance(config, dict) else ""
    return wanted if wanted in SCOPES else "site"


def check(login_id, password, extra=None):
    """(login id, None) when the pair is right, else (None, reason to show)."""
    login_id = normalize(login_id)
    now = time.time()
    with _failures_lock:
        recent = [t for t in _failures.get(login_id, []) if now - t < FAILURE_WINDOW]
        _failures[login_id] = recent
        if len(recent) >= MAX_FAILURES and now - recent[-1] < LOCK_SECONDS:
            wait = int(LOCK_SECONDS - (now - recent[-1])) // 60 + 1
            return None, "Too many wrong tries. Try again in %d minute(s)." % wait
    stored = all_users(extra).get(login_id)
    if _matches(stored or _DUMMY, password or "") and stored:
        with _failures_lock:
            _failures.pop(login_id, None)
        return login_id, None
    with _failures_lock:
        if len(_failures) > MAX_TRACKED_IDS:
            # Someone is trying lots of made-up IDs: forget the stale ones so
            # the list cannot grow without end.
            for key in [k for k, times in _failures.items()
                        if not times or now - times[-1] > FAILURE_WINDOW]:
                del _failures[key]
        _failures.setdefault(login_id, []).append(now)
    return None, "Wrong ID or password."


# -- changing the list (command line only) -------------------------------------
def set_user(login_id, password):
    login_id = normalize(login_id)
    if not login_id or any(c.isspace() for c in login_id):
        raise ValueError("The ID cannot be empty or contain spaces.")
    if len(password) < MIN_PASSWORD:
        raise ValueError("Use a password of at least %d characters." % MIN_PASSWORD)
    users = _file_users()
    users[login_id] = {"hash": hash_password(password),
                       "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_json(USERS_FILE, {"users": users})
    return login_id


def remove_user(login_id):
    users = _file_users()
    if users.pop(normalize(login_id), None) is None:
        return False
    save_json(USERS_FILE, {"users": users})
    return True


def file_user_ids():
    return sorted(_file_users())
