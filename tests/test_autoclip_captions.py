"""Auto clips (finding the best moments) and the new caption looks."""

import re
import subprocess
from pathlib import Path

import pytest

from clipdl import autoclip, captions, shorts, titles

FFMPEG = shorts.find_ffmpeg()


@pytest.fixture(autouse=True)
def caption_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(captions, "SETTINGS", tmp_path / "captions.json")


# -- picking moments -------------------------------------------------------------------
def test_replay_graph_becomes_a_value_a_second():
    item = {"heatmap": [{"start_time": 0, "end_time": 10, "value": 0.2},
                        {"start_time": 10, "end_time": 20, "value": 1.0}]}
    curve = autoclip.replay_curve(item, 20)
    assert curve[5] == 0.2 and curve[15] == 1.0
    assert autoclip.replay_curve({"heatmap": []}, 20) is None


def test_the_best_moments_win_and_never_overlap():
    score = [0.1] * 1200
    for t in range(300, 330):
        score[t] = 1.0                      # the big moment
    for t in range(800, 820):
        score[t] = 0.8                      # the second one
    for t in range(310, 340):
        score[t] = max(score[t], 0.9)       # right beside the first: must not be picked too
    moments = autoclip.pick(score, 2, 30)
    starts = sorted(start for start, _end, _v in moments)
    assert abs(starts[0] - 305) <= 10 and abs(starts[1] - 795) <= 10
    assert all(end - start == 30 for start, end, _v in moments)


def test_intros_are_skipped():
    score = [0.0] * 600
    for t in range(0, 40):
        score[t] = 1.0                      # "starting soon" screen at full volume
    for t in range(400, 430):
        score[t] = 0.5
    (start, _end, _value), = autoclip.pick(score, 1, 30)
    assert start >= autoclip.SKIP_START


def test_signals_are_blended_by_rank():
    replayed = [0.0] * 100
    loud = [-60.0] * 100                    # dB: quiet is very negative
    replayed[50], loud[50] = 1.0, -8.0      # rewatched and loud
    loud[10] = -3.0                         # louder, but nobody rewatched it
    score = autoclip.blend({"replayed": replayed, "chat": None, "loud": loud}, 100)
    assert score.index(max(score)) == 50
    flat = autoclip.blend({"replayed": [0.5] * 100, "chat": None, "loud": None}, 100)
    assert len(set(flat)) == 1              # a flat signal favours no second over another


def test_a_bump_beats_a_high_but_flat_stretch():
    # Hour one is busy all along; a short spike in quiet hour three is the highlight.
    replayed = [0.8] * 600 + [0.2] * 600
    for t in range(900, 930):
        replayed[t] = 0.6
    (start, _end, _value), = autoclip.pick(autoclip.blend({"replayed": replayed}, 1200), 1, 30)
    assert 880 <= start <= 920


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed")
def test_the_loudest_seconds_are_found(tmp_path):
    sound = tmp_path / "sound.m4a"
    # 60 s of quiet tone with a loud burst from 30 to 35 s.
    subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=60", "-af",
                    "volume='if(between(t,30,35),1,0.02)':eval=frame", "-c:a", "aac",
                    str(sound)], check=True)
    values = autoclip.levels(sound, 60)
    assert len(values) == 60
    assert 30 <= values.index(max(values)) <= 37


def test_notes_quote_what_was_said():
    item = {"title": "Big stream", "channel": "Streamer", "url":
            "https://www.youtube.com/watch?v=abc"}
    words = [(0, 1, "No"), (1, 2, "way"), (2, 3, "he"), (3, 4, "did"), (4, 5, "that!")]
    text = autoclip.notes(item, 125, words, ["most replayed"])
    assert "“No way he did that!”" in text
    assert "watch?v=abc&t=125s" in text and "most replayed" in text


# -- captions --------------------------------------------------------------------------
WORDS = [(0.0, 0.3, "no"), (0.3, 0.6, "way"), (0.6, 1.0, "bro!"),
         (2.0, 2.3, "look"), (2.3, 2.7, "at"), (2.7, 3.0, "this")]


def test_lines_break_at_sentences_and_pauses():
    groups = captions.groups(WORDS)
    assert [" ".join(w for _s, _e, w in g) for g in groups] == ["no way bro!", "look at this"]


def ass_events(path):
    return [line.split(",", 9)[1:3] + [line.split(",", 9)[9]]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("Dialogue:")]


def test_pop_lights_up_each_word_without_gaps(tmp_path):
    path = captions.write_ass(captions.chunks(WORDS), tmp_path / "c.ass",
                              config=dict(captions.DEFAULTS, style="pop"),
                              line_groups=captions.groups(WORDS))
    events = ass_events(path)
    assert len(events) == 6                          # one per word
    assert events[0][1] == events[1][0]              # each hands over to the next
    lit = [re.search(r"\\c&H[0-9A-F]+&?\\fscx112\\fscy112}(\w+[!]?)", e[2]).group(1)
           for e in events]
    assert lit == ["NO", "WAY", "BRO!", "LOOK", "AT", "THIS"]


def test_karaoke_sweeps_and_classic_is_plain(tmp_path):
    karaoke = ass_events(captions.write_ass(
        captions.chunks(WORDS), tmp_path / "k.ass", config=dict(captions.DEFAULTS,
                                                                style="karaoke"),
        line_groups=captions.groups(WORDS)))
    assert len(karaoke) == 2 and karaoke[0][2].count("\\kf") == 3
    classic = ass_events(captions.write_ass(
        captions.chunks(WORDS), tmp_path / "p.ass", config=dict(captions.DEFAULTS,
                                                                style="classic", caps=False),
        line_groups=captions.groups(WORDS)))
    assert [e[2] for e in classic] == ["no way bro!", "look at this"]


def test_position_and_box_are_in_the_style(tmp_path):
    text = captions.write_ass(captions.chunks(WORDS), tmp_path / "b.ass",
                              config=dict(captions.DEFAULTS, style="boxed", position="top"),
                              line_groups=captions.groups(WORDS)).read_text(encoding="utf-8")
    style = next(line for line in text.splitlines() if line.startswith("Style: Cap"))
    fields = style.split(",")
    assert fields[15] == "3"                         # BorderStyle 3: an opaque box
    assert fields[18] == "8"                         # alignment: top centre


def test_model_follows_the_settings(monkeypatch):
    monkeypatch.setattr(captions, "gpu_ready", lambda: False)
    assert captions.model_name(dict(captions.DEFAULTS, model="auto")) == "small.en"
    monkeypatch.setattr(captions, "gpu_ready", lambda: True)
    assert captions.model_name(dict(captions.DEFAULTS, model="auto")) == "large-v3-turbo"
    assert captions.model_name(dict(captions.DEFAULTS, model="fast", language="auto")) == "base"


# -- the whole job, offline -------------------------------------------------------------
@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed")
def test_make_clips_end_to_end(tmp_path, monkeypatch):
    from clipdl import media
    source = tmp_path / "stream.mp4"
    subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30:duration=12", "-f", "lavfi", "-i",
                    "sine=frequency=500:duration=12", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", "-shortest", str(source)], check=True)
    item = {"title": "A long stream", "channel": "Streamer", "url":
            "https://www.youtube.com/watch?v=xyz", "duration": 14400, "live": "was_live"}
    monkeypatch.setattr(autoclip, "find_moments", lambda *a, **k: (item, [
        (600, 610, 0.95, ["most replayed", "chat burst"]), (7200, 7210, 0.9, ["loud moment"])]))
    monkeypatch.setattr(autoclip, "_fetch_piece", lambda url, start, end, work, rank, h: source)
    monkeypatch.setattr(autoclip, "saved_folder", lambda: (str(tmp_path / "out"), True))
    result = autoclip.make_clips(item["url"], 2, 10, with_captions=False, use_brand=False)
    assert len(result["clips"]) == 2
    for clip in result["clips"]:
        info = media.probe(clip["file"])
        assert (info["width"], info["height"]) == (1080, 1920)
        assert not Path(clip["file"]).with_suffix(".txt").exists()   # only the clip
        notes = titles.read_notes(clip["file"])
        assert "Full video: https://www.youtube.com/watch?v=xyz&t=" in notes
    assert [Path(c["file"]).name for c in result["clips"]] == ["1 Streamer.mp4",
                                                               "2 Streamer.mp4"]


# -- telling gameplay from loud talking ----------------------------------------------------
def test_highlight_words_are_found(monkeypatch):
    from clipdl import highlights
    said = [(10.0, 10.3, "let's"), (10.3, 10.6, "go!"), (40.0, 40.4, "that's"),
            (40.4, 40.8, "an"), (40.8, 41.2, "ace"), (70.0, 70.5, "1v3"),
            (71.0, 71.5, "clutch"), (100.0, 100.4, "nice"), (100.4, 100.8, "weather")]
    monkeypatch.setattr(highlights.captions, "available", lambda: True)
    monkeypatch.setattr(highlights.captions, "gpu_ready", lambda: True)
    monkeypatch.setattr(highlights.captions, "listen", lambda sound: iter(said))
    curve, heard = highlights.hype_curve("sound.m4a", 120, extra_words=["weather"])
    assert heard[10] == ["let's go"] and heard[40] == ["ace"]
    assert "1v3" in heard[70] and "clutch" in heard[71]
    assert "weather" in heard[100]                   # the user's own word
    assert curve[71] > curve[10] > curve[55] == 0    # clutch + 1v3 beat "let's go"; silence 0


def test_still_screens_are_turned_down():
    score = [0.5] * 600
    for t in range(100, 130):
        score[t] = 0.9                               # loud, but on a still screen (a lobby)
    for t in range(400, 430):
        score[t] = 0.8                               # a little less loud, lots happening
    motion = [0.0] * 300 + [10.0] * 300
    curves = {"loud": score}
    plain = autoclip.pick(autoclip.blend(curves, 600), 1, 30)[0][0]
    gated = autoclip.pick(autoclip.blend(curves, 600, gate=motion), 1, 30)[0][0]
    assert 90 <= plain <= 110 and 390 <= gated <= 410


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed")
def test_action_sound_is_not_just_loudness(tmp_path):
    from clipdl import highlights
    sound = tmp_path / "sound.m4a"
    # 0-20 s a loud low hum (like a voice), 30-33 s quieter noise bursts (like gunfire).
    subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=180:duration=60", "-f", "lavfi", "-i",
                    "anoisesrc=color=white:duration=60:amplitude=0.3", "-filter_complex",
                    "[0:a]volume='if(lt(t,20),1,0.01)':eval=frame[a];"
                    "[1:a]volume='if(between(t,30,33),1,0.001)':eval=frame[b];"
                    "[a][b]amix=inputs=2:normalize=0", "-c:a", "aac", str(sound)], check=True)
    loud = highlights.loud_levels(sound, 60)
    action = highlights.action_curve(sound, 60)
    assert loud.index(max(loud)) < 20                # plain loudness points at the hum
    assert 30 <= action.index(max(action)) <= 36     # the action sound points at the bursts
