"""Ready-to-paste titles, a description and hashtags for each clip.

Built from what the clip already says - its title, the streamer, the game -
plus the tags streamers of that game are using right now. Three title options
per clip, because the best hook depends on the moment. The description always
credits the streamer and links the original clip.
"""

import re
import zlib

# Short tags people actually search, for games with long names.
GAME_TAGS = {
    "grandtheftauto5": "gta5", "counterstrike": "cs2", "leagueoflegends": "leagueoflegends",
    "worldofwarcraft": "wow", "rainbow6siege": "r6", "callofdutywarzone": "warzone",
    "eafc27": "eafc", "apexlegends": "apex", "deadbydaylight": "dbd",
    "marvelrivals": "marvelrivals", "valorant": "valorant", "minecraft": "minecraft",
    "fortnite": "fortnite", "roblox": "roblox",
}
HOOKS = [
    (("ace", "clutch", "1v5", "1v4", "1v3", "4k", "5k", "quad", "penta", "insane", "crazy"),
     ["pulls off an insane clutch", "just did the impossible", "clutches it"]),
    (("jumpscare", "scare", "scream", "scared"),
     ["gets the jump scare of the year", "was NOT ready for this", "nearly falls off the chair"]),
    (("lol", "lmao", "funny", "laugh", "bruh", "💀", "😂"),
     ["can't stop laughing", "this had me dying", "this is too funny"]),
    (("rage", "mad", "tilt", "angry", "why"),
     ["completely loses it", "rage moment", "is so tilted"]),
    (("win", "won", "victory", "dub", "gg"),
     ["gets the win", "wins it at the last second", "secures the dub"]),
]
DEFAULT_HOOKS = ["has the craziest moment", "you have to see this", "did not expect this"]
GENERIC_TITLES = {"clip", "lol", "w", "l", "gg", "omg", "wtf", "bruh", "clipped", "lmao"}


def game_tag(game_key, game_name):
    return GAME_TAGS.get(game_key) or re.sub(r"[^A-Za-z0-9]", "", game_name or "").lower()[:30]


def _clean(title):
    title = re.sub(r"\s+", " ", (title or "").strip())
    return title.strip(" -|")


def suggest(clip_title, streamer, game_name, game_key="", clip_url="", trending=()):
    """{"titles": [3 options], "description": str, "hashtags": [..]}."""
    title = _clean(clip_title)
    words = title.lower().split()
    seed = zlib.crc32((clip_url or title or streamer).encode("utf-8"))
    tokens = set(re.findall(r"[a-z0-9']+", title.lower()))
    # Whole words only ("window" is not "win"); emoji keys match anywhere.
    hooks = next((h for keys, h in HOOKS
                  if any(k in tokens if k.isalnum() else k in title for k in keys)),
                 DEFAULT_HOOKS)
    hook = hooks[seed % len(hooks)]
    meaningful = len(words) >= 2 and title.lower() not in GENERIC_TITLES

    titles = []
    if meaningful:
        titles.append("%s: %s" % (streamer, title[:80]))
    titles.append("%s %s in %s" % (streamer, hook, game_name) if game_name
                  else "%s %s" % (streamer, hook))
    titles.append("%s moment of the day 😳 (%s)" % (game_name or "Gaming", streamer))
    if not meaningful:
        titles.append("%s %s" % (streamer, hooks[(seed + 1) % len(hooks)]))
    titles = [t[:95] for t in titles][:3]

    tags = ["shorts", "gaming", "twitch"]
    main = game_tag(game_key, game_name)
    if main:
        tags.insert(0, main)
    handle = re.sub(r"[^A-Za-z0-9_]", "", streamer or "").lower()
    if handle:
        tags.append(handle)
    for tag in trending or ():
        clean = re.sub(r"[^A-Za-z0-9]", "", str(tag)).lower()
        if clean and clean not in tags and len(tags) < 9:
            tags.append(clean)
    hashtags = ["#" + t for t in tags]

    description = "%s on Twitch%s\n%s\n\nAll credit to %s - go follow them!\n\n%s" % (
        streamer, (": twitch.tv/" + handle) if handle else "",
        ("Original clip: " + clip_url) if clip_url else "", streamer, " ".join(hashtags))
    return {"titles": titles, "description": description.replace("\n\n\n", "\n\n"),
            "hashtags": hashtags}


def sidecar_text(suggestion):
    lines = ["TITLE OPTIONS"] + ["  %d. %s" % (i, t) for i, t in
                                 enumerate(suggestion["titles"], 1)]
    lines += ["", "DESCRIPTION", suggestion["description"], "", "HASHTAGS",
              " ".join(suggestion["hashtags"])]
    return "\n".join(lines) + "\n"


def write_sidecar(video_path, suggestion):
    """Save the suggestions as <video name>.txt beside the video."""
    path = video_path.with_suffix(".txt")
    try:
        path.write_text(sidecar_text(suggestion), encoding="utf-8")
    except OSError:
        return None
    return path
