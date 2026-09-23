"""Branding & hooks: your logo on every Short, a hook line on screen for the
first seconds ("WAIT FOR IT..."), and an intro and outro clip.

Set on the Studio page; kept in data/branding.json with the files in
data/branding/. When switched on, every Short the downloader, the clip radar,
the streamer page and the Autopilot make gets it; the Studio can use it on
single Shorts and compilations too.
"""

from pathlib import Path

from . import media
from .captions import ASS_HEADER, _escape, _stamp
from .config import DATA_DIR
from .util import load_json, save_json

FOLDER = DATA_DIR / "branding"
SETTINGS = DATA_DIR / "branding.json"
POSITIONS = {"top-right": "W-w-40:40", "top-left": "40:40", "top-centre": "(W-w)/2:40",
             "bottom-right": "W-w-40:H-h-300", "bottom-left": "40:H-h-300"}
DEFAULTS = {"enabled": False, "logo_file": "", "logo_pos": "top-right", "logo_size": 18,
            "logo_opacity": 0.85, "hook_on": False, "hook_text": "WAIT FOR IT…",
            "hook_seconds": 3.0, "intro": False, "outro": False}
MAX_EXTRA_SECONDS = 15          # intros and outros longer than this are cut
HOOK_STYLE = ("Style: Hook,Arial Black,{size},&H0000F0FF,&H000000FF,&H00000000,&H96000000,"
              "-1,0,0,0,100,100,0,0,1,8,3,8,60,60,{margin},1\n")


def settings():
    data = load_json(SETTINGS, {})
    config = dict(DEFAULTS, **(data if isinstance(data, dict) else {}))
    if config["logo_pos"] not in POSITIONS:
        config["logo_pos"] = DEFAULTS["logo_pos"]
    return config


def save_settings(**changes):
    save_json(SETTINGS, dict(settings(), **changes))


def logo_path(config=None):
    config = config or settings()
    path = FOLDER / config["logo_file"] if config["logo_file"] else None
    return path if path and path.exists() else None


def extra_path(kind, shape):
    """The intro/outro made to fit: kind "intro"/"outro", shape "v" (9:16) / "h" (16:9)."""
    path = FOLDER / ("%s_%s.mp4" % (kind, shape))
    return path if path.exists() else None


# -- saving what was uploaded ----------------------------------------------------------
def save_logo(data, suffix):
    suffix = suffix.lower() if suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") else ".png"
    FOLDER.mkdir(parents=True, exist_ok=True)
    for old in FOLDER.glob("logo.*"):
        old.unlink(missing_ok=True)
    (FOLDER / ("logo" + suffix)).write_bytes(data)
    save_settings(logo_file="logo" + suffix)


def remove_logo():
    for old in FOLDER.glob("logo.*"):
        old.unlink(missing_ok=True)
    save_settings(logo_file="")


def save_extra(kind, data, suffix):
    """Store an intro or outro, made once to fit Shorts (9:16) and videos (16:9).
    Returns None, or why it could not be used."""
    FOLDER.mkdir(parents=True, exist_ok=True)
    raw = FOLDER / ("%s_upload%s" % (kind, suffix.lower() or ".mp4"))
    raw.write_bytes(data)
    try:
        for shape, (width, height) in (("v", (1080, 1920)), ("h", (1920, 1080))):
            problem = media.fit(raw, FOLDER / ("%s_%s.mp4" % (kind, shape)), width, height,
                                end=MAX_EXTRA_SECONDS)
            if problem:
                return problem
    finally:
        raw.unlink(missing_ok=True)
    save_settings(**{kind: True})
    return None


def remove_extra(kind):
    for shape in ("v", "h"):
        (FOLDER / ("%s_%s.mp4" % (kind, shape))).unlink(missing_ok=True)
    save_settings(**{kind: False})


# -- using it --------------------------------------------------------------------------
def write_hook(folder, text, seconds, width=1080, height=1920, margin=260, size=96):
    """The hook line as an .ass file in `folder` (top of the frame)."""
    header = ASS_HEADER.format(w=width, h=height, size=size, margin=margin)
    header = header.replace("\n[Events]", HOOK_STYLE.format(size=size, margin=margin)
                            + "\n[Events]")
    path = Path(folder) / "hook.ass"
    path.write_text(header + "Dialogue: 0,%s,%s,Hook,,0,0,0,,{\\fad(150,250)}%s\n"
                    % (_stamp(0), _stamp(seconds), _escape(text)), encoding="utf-8")
    return path


def for_short(work_folder, config=None, force=False):
    """What make_short needs: {"overlay", "hook", "intro", "outro"} - or None when
    branding is off. `work_folder` is where the hook's .ass goes (the captions'
    folder, since ffmpeg reads them all from one place)."""
    config = config or settings()
    if not (config["enabled"] or force):
        return None
    logo = logo_path(config)
    return {
        "overlay": (logo, POSITIONS[config["logo_pos"]], int(1080 * config["logo_size"] / 100),
                    float(config["logo_opacity"])) if logo else None,
        "hook": write_hook(work_folder, config["hook_text"], float(config["hook_seconds"]))
        if config["hook_on"] and config["hook_text"].strip() else None,
        "intro": extra_path("intro", "v") if config["intro"] else None,
        "outro": extra_path("outro", "v") if config["outro"] else None,
    }


def wrap(video, brand):
    """Put the intro and outro around a finished video, in place. Returns None or
    a reason (the video is then left as it was)."""
    extras = [p for p in ((brand or {}).get("intro"), (brand or {}).get("outro")) if p]
    if not extras:
        return None
    video = Path(video)
    if not media.probe(video)["audio"]:
        return "the clip has no sound track"
    parts = ([brand["intro"]] if brand.get("intro") else []) + [video] + (
        [brand["outro"]] if brand.get("outro") else [])
    joined = video.with_name(video.stem + ".wrap.mp4")
    problem = media.join_encoding(parts, joined)
    if problem:
        joined.unlink(missing_ok=True)
        return problem
    joined.replace(video)
    return None
