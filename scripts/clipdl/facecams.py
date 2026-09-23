"""Where each streamer's camera sits, for the facecam + gameplay Short layout.

Every streamer puts their webcam somewhere else, so it is marked once per
streamer - as fractions of the frame (x, y, width, height), which fit any
resolution - and remembered in data/facecams.json.
"""

import time

from .config import DATA_DIR
from .util import load_json, save_json

FILE = DATA_DIR / "facecams.json"
# Quick starting points for the marking screen: (label, x, y, w, h).
PRESETS = [("Top left", 0.0, 0.0, 0.26, 0.30), ("Top right", 0.74, 0.0, 0.26, 0.30),
           ("Bottom left", 0.0, 0.66, 0.26, 0.34), ("Bottom right", 0.74, 0.66, 0.26, 0.34)]


def load():
    data = load_json(FILE, {})
    return data if isinstance(data, dict) else {}


def _key(name=None, user_id=None):
    return "id:%s" % user_id if user_id else "name:%s" % (name or "").strip().lower()


def region(name=None, user_id=None):
    """(x, y, w, h) fractions for a streamer, or None when not marked."""
    data = load()
    entry = (data.get(_key(user_id=user_id)) if user_id else None) or data.get(_key(name=name))
    if not entry:
        return None
    return tuple(float(entry[k]) for k in ("x", "y", "w", "h"))


def save(name, box, user_id=None):
    x, y, w, h = (max(0.0, min(1.0, float(v))) for v in box)
    if w < 0.05 or h < 0.05:
        raise ValueError("The camera box is too small.")
    data = load()
    data.pop(_key(name=name), None)
    data[_key(name, user_id)] = {"name": name, "id": user_id, "x": x, "y": y,
                                 "w": min(w, 1 - x), "h": min(h, 1 - y),
                                 "updated": int(time.time())}
    save_json(FILE, data)


def remove(name, user_id=None):
    data = load()
    if user_id:
        data.pop(_key(user_id=user_id), None)
    data.pop(_key(name=name), None)
    save_json(FILE, data)


def entries():
    return sorted(load().values(), key=lambda e: (e.get("name") or "").lower())


def preview(image_bytes, box, width=270):
    """(frame with the camera box drawn, the Short it makes) as PNG bytes, for the
    marking screen. Same crop maths as the split look in shorts.py, on a still."""
    import io

    from PIL import Image, ImageDraw
    frame = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    fw, fh = frame.size
    x, y, w, h = box
    left, top, right, bottom = (int(x * fw), int(y * fh), int((x + w) * fw), int((y + h) * fh))
    marked = frame.copy()
    draw = ImageDraw.Draw(marked)
    for inset in range(3):
        draw.rectangle([left + inset, top + inset, right - inset, bottom - inset],
                       outline=(145, 70, 255))

    short_w, short_h, cam_h = 1080, 1920, 640
    cam = frame.crop((left, top, max(right, left + 1), max(bottom, top + 1)))
    scale = max(short_w / cam.width, cam_h / cam.height)
    cam = cam.resize((int(cam.width * scale) + 1, int(cam.height * scale) + 1))
    cam = cam.crop(((cam.width - short_w) // 2, (cam.height - cam_h) // 2,
                    (cam.width - short_w) // 2 + short_w, (cam.height - cam_h) // 2 + cam_h))
    game_h = short_h - cam_h
    crop_w = int(fh * short_w / game_h)
    game = frame.crop(((fw - crop_w) // 2, 0, (fw - crop_w) // 2 + crop_w, fh))
    game = game.resize((short_w, game_h))
    short = Image.new("RGB", (short_w, short_h))
    short.paste(cam, (0, 0))
    short.paste(game, (0, cam_h))
    short = short.resize((width, int(width * short_h / short_w)))

    def png(image):
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    return png(marked), png(short)
