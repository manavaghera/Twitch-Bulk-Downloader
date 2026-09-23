"""Which streamers allow their clips to be reposted - the permission list.

Reposting clips without permission is how clip channels get copyright strikes.
Many streamers say on their channel page whether clipping is fine; this keeps
what you found out, and lets downloads use only the streamers you trust.

Kept in data/permissions.json, keyed by Twitch user id when known (display
names can change) and by lower-case name otherwise.
"""

import time

from .config import DATA_DIR
from .util import load_json, save_json

FILE = DATA_DIR / "permissions.json"
STATUSES = {"allowed": "✅ Allowed", "ask": "❔ Not sure yet", "blocked": "⛔ Don't use"}
# What a download may use: (key, label).
MODES = [("not_blocked", "Everyone except streamers marked Don't use"),
         ("allowed_only", "Only streamers marked Allowed"),
         ("any", "Everyone (ignore the list)")]


def load():
    data = load_json(FILE, {})
    return data if isinstance(data, dict) else {}


def _key(name=None, user_id=None):
    return "id:%s" % user_id if user_id else "name:%s" % (name or "").strip().lower()


def status_of(name=None, user_id=None):
    """"allowed", "ask", "blocked" or None for a streamer."""
    data = load()
    entry = data.get(_key(user_id=user_id)) if user_id else None
    entry = entry or data.get(_key(name=name))
    return (entry or {}).get("status")


def set_status(name, status, user_id=None, note=""):
    if status not in STATUSES:
        raise ValueError("Unknown status %r" % status)
    data = load()
    data.pop(_key(name=name), None)          # one entry per streamer, by id when known
    data[_key(name, user_id)] = {"name": name, "id": user_id, "status": status, "note": note,
                                  "updated": int(time.time())}
    save_json(FILE, data)


def remove(name, user_id=None):
    data = load()
    if user_id:
        data.pop(_key(user_id=user_id), None)
    data.pop(_key(name=name), None)
    save_json(FILE, data)


def entries():
    """[{name, id, status, note, updated}] sorted by name."""
    return sorted(load().values(), key=lambda e: (e.get("name") or "").lower())


def allow_filter(mode):
    """A clip -> bool check for a download mode, or None when nothing is filtered."""
    if mode == "any":
        return None
    data = load()
    ids = {e.get("id"): e.get("status") for e in data.values() if e.get("id")}
    names = {(e.get("name") or "").lower(): e.get("status") for e in data.values()}

    def allowed(clip):
        status = ids.get(clip.get("broadcaster_id")) or \
            names.get((clip.get("broadcaster_name") or "").lower())
        return status == "allowed" if mode == "allowed_only" else status != "blocked"
    return allowed
