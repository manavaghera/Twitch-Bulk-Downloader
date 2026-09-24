"""Finding plays (VALORANT kills), placing clips, the AI's second opinion, and
keeping each clip's sound on its picture."""

import subprocess

import numpy as np
import pytest

from clipdl import autoclip, judge, kills, moments, shorts, synccheck, ytpiece

FFMPEG = shorts.find_ffmpeg()
H, W = kills.HIGH, kills.WIDE


# -- kills on screen -----------------------------------------------------------------------
def _background(rng):
    """A busy, lopsided game-world patch."""
    patch = rng.normal(90, 25, (H, W))
    patch[:, 40:] += 60                              # a gun on the right, say
    return patch


def _emblem(patch, stretch=1.0):
    """A kill emblem: a ring, a dark backdrop, a light symmetric skull."""
    y, x = np.mgrid[0:H, 0:W]
    dx, dy = (x - (W - 1) / 2.0) / stretch, y - (H - 1) / 2.0
    r = np.hypot(dx, dy)
    patch = patch.copy()
    patch[r < 19] = 40                               # the backdrop
    patch[np.abs(r - 17) < 1.2] = 230                # the ring
    patch[(r < 9) & (dy < 4)] = 210                  # the skull
    patch[(np.abs(np.abs(dx) - 3.5) < 1.5) & (np.abs(dy + 1) < 1.5)] = 30   # its eyes
    return patch


def _frames(seconds, emblems_at, rng, stretch=1.0, lasting=1.5):
    frames = []
    for i in range(int(seconds * kills.FPS)):
        t = i / float(kills.FPS)
        patch = _background(rng)
        if any(at <= t < at + lasting for at in emblems_at):
            patch = _emblem(patch, stretch)
        frames.append(np.clip(patch, 0, 255))
    return np.array(frames, dtype=np.uint8)


def test_emblems_score_high_and_lookalikes_do_not():
    rng = np.random.default_rng(1)
    plain = np.array([np.clip(_background(rng), 0, 255) for _ in range(4)], dtype=np.uint8)
    emblem = np.array([np.clip(_emblem(_background(rng)), 0, 255)], dtype=np.uint8)
    stretched = np.array([np.clip(_emblem(_background(rng), 1.33), 0, 255)], dtype=np.uint8)
    y, x = np.mgrid[0:H, 0:W]
    blob = np.where(np.hypot(x - (W - 1) / 2.0, y - (H - 1) / 2.0) < 20, 200, 90)
    spike = np.array([blob], dtype=np.uint8)           # the spike: symmetric, but one smooth shape
    score = kills.emblem_score(np.concatenate([plain, emblem, stretched, spike]))
    assert score[4] > kills.ON and score[5] > kills.ON
    assert max(score[:4]) < 0.2 and score[6] < 0.2


def test_kills_become_plays_and_a_clutch(monkeypatch):
    rng = np.random.default_rng(2)
    frames = _frames(120, [30, 36, 43, 90], rng)
    monkeypatch.setattr(kills, "patches", lambda video: frames)
    found = kills.find_kills("watch.mp4")
    assert [round(t) for t, _sure in found] == [30, 36, 43, 90]
    assert kills.plays(found) == [(30.0, 43.0, 3), (90.0, 90.0, 1)]
    curve, labels = kills.play_curve(found, 120)
    assert labels == {30: "3K"} and curve[35] == kills.PLAY_VALUE[3]
    assert curve[22] == kills.PLAY_VALUE[3] * 0.8 and curve[21] == 0   # the lead-up, a bit less
    assert curve[90] == kills.PLAY_VALUE[1]
    _curve, labels = kills.play_curve(found, 120, last_alive=[25])
    assert labels[30] == "clutch (3K)"
    # Kills while dead are a teammate's (watched after dying): not this player's play.
    curve, labels = kills.play_curve(found, 120, dead=[(34, 50)])
    assert labels == {} and curve[31] == kills.PLAY_VALUE[1]


def test_not_valorant_gives_no_kills(monkeypatch):
    rng = np.random.default_rng(3)
    # Something symmetric every few seconds, far more often than anyone kills: not emblems.
    frames = _frames(120, list(range(0, 120, 3)), rng)
    monkeypatch.setattr(kills, "patches", lambda video: frames)
    assert kills.find_kills("watch.mp4") == []


def test_valorant_is_told_from_the_title_and_tags():
    assert kills.is_valorant({"title": "Radiant grind - VALORANT ranked"})
    assert kills.is_valorant({"title": "ranked", "_info": {"tags": ["jett", "vandal", "ascent"]}})
    assert not kills.is_valorant({"title": "Minecraft hardcore day 100"})


# -- placing the clips -----------------------------------------------------------------------
def test_the_action_sits_just_before_the_middle():
    score = [0.1] * 1200
    for t in range(500, 506):
        score[t] = 1.0
    (start, end, _v), = moments.pick(score, 1, 30)
    assert 485 <= start <= 492 and end - start == 30


def test_a_three_kill_play_beats_talking():
    seconds = 1200
    hype = [0.0] * seconds
    for t in range(300, 310):
        hype[t] = 3.0                                 # someone said something hyped
    play = [0.0] * seconds
    for t in range(800, 820):
        play[t] = kills.PLAY_VALUE[3]                 # a 3K
    score = moments.blend({"hype": hype, "kills": play}, seconds)
    (start, _end, _v), = moments.pick(score, 1, 30)
    assert 780 <= start <= 810


def test_edges_move_to_pauses_in_the_talking():
    words = [(95.0, 95.4, "okay"), (95.5, 96.0, "so"), (96.1, 96.6, "we"), (96.7, 97.2, "go"),
             (97.3, 97.8, "now"), (120.0, 120.4, "that"), (120.5, 121.0, "was"),
             (121.1, 122.0, "insane")]
    assert moments.snap(96, 121, words, 600) == (94, 123)   # both were mid-sentence
    assert moments.snap(100, 110, words, 600) == (100, 110)  # already quiet: left alone


# -- the AI's second opinion ------------------------------------------------------------------
class _Reply:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def test_the_judge_rates_through_ollama(monkeypatch):
    asked = {}
    monkeypatch.setattr(judge.requests, "get", lambda url, timeout: _Reply(
        {"models": [{"name": "qwen2.5-coder:32b"}, {"name": "llama3:8b-instruct-q4_0"}]}))

    def post(url, timeout, json):
        asked.update(json)
        return _Reply({"message": {"content": '{"score": 8, "kind": "funny", "why": "he '
                                              'blames his mouse"}'}})
    monkeypatch.setattr(judge.requests, "post", post)
    assert judge.model() == "llama3:8b-instruct-q4_0"   # not the coding model
    assert judge.rate("my mouse died bro", "2K") == (0.8, "funny", "he blames his mouse")
    assert "On screen: 2K" in asked["messages"][1]["content"]


def test_no_ollama_means_no_opinion(monkeypatch):
    def refuse(*a, **k):
        raise judge.requests.ConnectionError("not running")
    monkeypatch.setattr(judge.requests, "get", refuse)
    assert judge.model() is None
    assert judge.rate_all([("text", "")]) == [None]


def test_the_opinion_reorders_the_moments(monkeypatch):
    ready = [(100, 130, 0.9, ["what was said"]), (400, 430, 0.8, [])]
    words = [(105.0, 105.5, "callout"), (410.0, 410.5, "hilarious")]
    monkeypatch.setattr(judge, "rate_all", lambda stretches, name: [
        (0.1, "boring", "callouts"), (1.0, "funny", "a real joke")])
    autoclip._second_opinion(ready, words, "llama3")
    best = max(ready, key=lambda moment: moment[2])
    assert best[0] == 400 and "AI: funny 10/10 - a real joke" in best[3]


def test_hyped_chat_messages_are_found():
    action = {"actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
        "message": {"runs": [{"text": "KEKW "}, {"emoji": {"shortcuts": [":joy:"]}}]}}}}}]}
    assert autoclip._chat_text(action) == "KEKW  :joy:"
    assert autoclip.CHAT_HYPE.search("KEKW") and autoclip.CHAT_HYPE.search("clip it!!!")
    assert not autoclip.CHAT_HYPE.search("what time is it in london")


# -- sound on the picture -----------------------------------------------------------------------
def test_pieces_are_cut_from_their_own_start():
    graph = ytpiece.exact_cut(3.25, 1.5, 30)
    assert "trim=start=3.250:duration=30.000" in graph
    assert "atrim=start=1.500:duration=30.000" in graph
    assert "-ss" not in graph                        # never a jump into the part
    assert ytpiece._fine(["-c:v", "h264_nvenc", "-cq", "27"])[-1] == "19"


def _make(path, seconds, extra=()):
    """A test video whose picture and sound change at uneven moments, together."""
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "life=size=160x90:rate=30:seed=7:ratio=0.2,scale=320:180,format=yuv420p",
                    "-f", "lavfi", "-i",
                    "aevalsrc='0.6*sin(440*2*PI*t)*gt(sin(1.7*t*t),0.6)':s=44100",
                    "-t", str(seconds)] + list(extra) + [
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)],
                   check=True)


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed")
def test_a_slipped_clip_is_found_and_lined_up(tmp_path):
    whole = tmp_path / "whole.mp4"
    _make(whole, 60)
    good = tmp_path / "good.mp4"
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-ss", "20", "-i", str(whole), "-t", "20",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(good)], check=True)
    heard, seen, gap = synccheck.check(good, 20, whole, whole)
    assert abs(heard) < 0.05 and abs(seen) < 0.15 and abs(gap) < 0.05
    piece, note = synccheck.ensure(good, 20, whole, whole)
    assert piece == good and "in sync" in note

    late = tmp_path / "late.mp4"                    # the voice 1.2 s behind the picture
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-ss", "20", "-i", str(whole),
                    "-ss", "18.8", "-i", str(whole), "-t", "20", "-map", "0:v", "-map", "1:a",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(late)],
                   check=True)
    heard, seen, _gap = synccheck.check(late, 20, whole, whole)
    assert abs((heard - seen) - (-1.2)) < 0.15 or abs((heard - seen) - 1.2) < 0.15
    fixed, note = synccheck.ensure(late, 20, whole, whole)
    assert fixed != late and "lined up" in note
    heard, seen, gap = synccheck.check(fixed, 20, whole, whole)
    assert abs(heard - seen) <= synccheck.TOLERANCE and abs(gap) <= synccheck.TOLERANCE


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed")
def test_a_picture_that_starts_late_is_made_to_start_with_the_sound(tmp_path):
    whole = tmp_path / "whole.mp4"
    _make(whole, 40)
    gap_file = tmp_path / "gap.mp4"                  # the picture starts 2 s in, the sound at 0
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-ss", "12", "-i", str(whole), "-ss",
                    "10", "-i", str(whole), "-t", "20", "-map", "0:v", "-map", "1:a",
                    "-output_ts_offset", "0", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-vf", "setpts=PTS+2/TB", str(gap_file)], check=True)
    _heard, _seen, gap = synccheck.check(gap_file, 10, whole, whole)
    assert abs(gap - 2.0) < 0.1
    fixed, _note = synccheck.ensure(gap_file, 10, whole, whole)
    heard, seen, gap = synccheck.check(fixed, 10, whole, whole)
    assert abs(gap) <= synccheck.TOLERANCE and abs(heard) < 0.05
    assert seen is None or abs(seen) <= synccheck.TOLERANCE   # the padded start is a still frame
