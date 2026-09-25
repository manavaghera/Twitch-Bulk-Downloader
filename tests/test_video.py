"""Branding, trimming and compilations on small generated videos (needs ffmpeg)."""

import json
import subprocess
from datetime import datetime, timezone

import pytest

from clipdl import branding, media, shorts, studio, titles

FFMPEG = shorts.find_ffmpeg()
pytestmark = pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed")


def make(path, seconds, size="1920x1080", rate=60, sound=True):
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
            "testsrc2=size=%s:rate=%d:duration=%s" % (size, rate, seconds)]
    if sound:
        args += ["-f", "lavfi", "-i", "sine=frequency=300:duration=%s" % seconds, "-c:a", "aac"]
    subprocess.run(args + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)],
                   check=True)
    return path


def decodes(path):
    done = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.returncode == 0 and not done.stderr.strip()


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(branding, "FOLDER", tmp_path / "branding")
    monkeypatch.setattr(branding, "SETTINGS", tmp_path / "branding.json")
    monkeypatch.setattr(studio, "CACHE", tmp_path / "cache")
    game = tmp_path / "VALORANT"
    game.mkdir()
    a = make(game / "001_Tarik_(ACE).mp4", 6)
    b = make(game / "002_한동숙_(clutch).mp4", 4, rate=30)        # a name in another script
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    clips = {
        "c1": {"status": "downloaded", "file": str(a), "title": "INSANE ACE", "streamer": "Tarik",
               "game": "VALORANT", "views": 9000, "url": "https://clips.twitch.tv/A",
               "created_at": now, "updated_at": now},
        "c2": {"status": "downloaded", "file": str(b), "title": "clutch", "streamer": "한동숙",
               "game": "VALORANT", "views": 5000, "url": "https://clips.twitch.tv/B",
               "created_at": now, "updated_at": now},
        "c3": {"status": "downloaded", "file": str(game / "gone.mp4"), "title": "gone",
               "streamer": "X", "game": "VALORANT", "views": 7000, "url": "",
               "created_at": now, "updated_at": now}}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"clips": clips}), encoding="utf-8")
    monkeypatch.setattr(studio, "MANIFEST_FILE", manifest)
    return tmp_path


def test_probe_reads_a_video(library):
    info = media.probe(library / "VALORANT" / "002_한동숙_(clutch).mp4")
    assert (info["width"], info["height"], info["audio"]) == (1920, 1080, True)
    assert abs(info["duration"] - 4) < 0.2


def test_branded_short_has_intro_logo_and_hook(library):
    intro = make(library / "intro.mp4", 2, size="720x1280", rate=25, sound=False)
    assert branding.save_extra("intro", intro.read_bytes(), ".mp4") is None
    assert media.probe(branding.extra_path("intro", "h"))["audio"]     # silence added
    logo = library / "logo.png"
    subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "color=c=yellow:size=300x120",
                    "-frames:v", "1", str(logo)], check=True)
    branding.save_logo(logo.read_bytes(), ".png")
    branding.save_settings(enabled=True, hook_on=True, hook_text="WAIT FOR IT", hook_seconds=2)
    work = library / "work"
    work.mkdir()
    brand = branding.for_short(work)
    assert brand["overlay"] and brand["hook"] and brand["intro"]
    out = library / "Shorts" / "clip.mp4"
    source = library / "VALORANT" / "001_Tarik_(ACE).mp4"
    assert shorts.make_short(source, out, "blur", None, None, brand) is None
    info = media.probe(out)
    assert (info["width"], info["height"]) == (1080, 1920) and abs(info["duration"] - 8) < 0.3
    assert decodes(out)


def test_trim_makes_a_short_with_title_file(library):
    entry = next(r for r in studio.library() if r["id"] == "c1")
    path, problem = studio.trim_short(entry, 1.0, 4.0)
    assert problem is None
    info = media.probe(path)
    assert (info["width"], info["height"]) == (1080, 1920) and abs(info["duration"] - 3) < 0.2
    assert not path.with_suffix(".txt").exists()                     # only the video
    assert "TITLE OPTIONS" in titles.read_notes(path)                # the ideas, kept by the app


def test_weekly_compilation(library):
    picked = studio.weekly_pick(7, 10)
    assert [r["id"] for r in picked] == ["c1", "c3", "c2"]
    result = studio.compilation(picked, "Top clips", countdown=True,
                                folder=library / "Compilations")
    assert result["skipped"] == ["gone"]
    info = media.probe(result["video"])
    assert (info["width"], info["height"]) == (1920, 1080) and abs(info["duration"] - 10) < 0.3
    assert decodes(result["video"])
    notes = titles.read_notes(result["video"])                       # kept by the app
    assert notes == result["notes"] and not result["video"].with_suffix(".txt").exists()
    assert "0:00 #3 한동숙" in notes and "0:04 #1 Tarik" in notes
    assert "https://twitch.tv/tarik" in notes
