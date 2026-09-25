from clipdl import captions
from clipdl.trend_gather import _title_fits
from clipdl.web import normalize_name


def test_caption_lines_stay_short():
    words = [(i * 0.3, i * 0.3 + 0.25, w) for i, w in
             enumerate("this is a really long sentence that keeps going on".split())]
    lines = captions.chunks(words)
    assert all(len(text.split()) <= captions.WORDS_PER_LINE for _s, _e, text in lines)
    assert " ".join(t for _s, _e, t in lines) == \
        "this is a really long sentence that keeps going on"


def test_a_word_cannot_hang_over_silence():
    lines = captions.chunks([(0.0, 6.0, "hello")])
    assert lines[0][1] - lines[0][0] <= captions.MAX_WORD_SECONDS + 1e-9


def test_short_gaps_are_closed_so_lines_do_not_flicker():
    words = [(0.0, 0.5, "one"), (0.5, 1.0, "two"), (1.0, 1.4, "three"),
             (1.5, 1.9, "four")]
    lines = captions.chunks(words)
    assert len(lines) == 2
    assert lines[0][1] == lines[1][0]


def test_ass_file_escapes_braces(tmp_path):
    path = captions.write_ass([(0.0, 1.0, "hi {there}")], tmp_path / "c.ass")
    text = path.read_text(encoding="utf-8")
    assert "Dialogue: 0,0:00:00.00,0:00:01.00" in text
    assert "HI (THERE)" in text and "{there}" not in text


def test_names_match_across_sites():
    assert normalize_name("HELLDIVERS™ 2") == normalize_name("Helldivers 2")
    assert normalize_name("Red Dead Redemption II") == normalize_name("Red Dead Redemption 2")
    assert normalize_name("Grand Theft Auto V (GTA)") == normalize_name("Grand Theft Auto V")


def test_wikipedia_title_must_name_the_game():
    assert _title_fits("Helldivers 2", "Helldivers 2")
    assert _title_fits("Hades II", "Hades II (video game)")
    assert not _title_fits("Witchbrook", "Chucklefish")


# -- clip file names ---------------------------------------------------------------------
class _History:
    """A download history (manifest) holding the given {clip id: file}."""

    def __init__(self, files):
        self.data = {"clips": {cid: {"file": str(path)} for cid, path in files.items()}}


def _clip(cid, streamer, title="a clip"):
    return {"id": cid, "broadcaster_name": streamer, "title": title,
            "url": "https://clips.twitch.tv/" + cid}


def test_clip_names_are_a_number_and_the_streamer(tmp_path):
    from clipdl import download
    from clipdl.util import short_title
    jobs = download.build_jobs([_clip("a", "Jynxzi", "I KILLED A PRO 😂"), _clip("b", "Tarik"),
                                _clip("c", "Jynxzi", "again")], tmp_path, "VALORANT")
    assert [job.path.name for job in jobs] == ["1 Jynxzi.mp4", "2 Tarik.mp4", "3 Jynxzi.mp4"]
    assert short_title("한동숙 clutch 🔥🔥 insane") == "한동숙 clutch insane"


def test_clips_already_downloaded_are_known_and_renumbered(tmp_path):
    from clipdl import download
    old = tmp_path / "013_Jynxzi_(I KILLED A PRO (Hiko) 😂).mp4"     # named the old way
    old.write_bytes(b"clip")
    mine = tmp_path / "1 Tarik.mp4"                                   # named the new way
    mine.write_bytes(b"clip")
    history = _History({"t": mine})
    jobs = download.build_jobs([_clip("t2", "Tarik"), _clip("t", "Tarik"),
                                _clip("j", "Jynxzi", "I KILLED A PRO (Hiko) 😂")],
                               tmp_path, "VALORANT", manifest=history)
    assert jobs[0].existing is None                   # another Tarik clip: not the same one
    assert jobs[1].existing == mine and jobs[1].path.name == "2 Tarik.mp4"
    assert jobs[2].existing == old and jobs[2].path.name == "3 Jynxzi.mp4"
    assert download.next_free_number(tmp_path) == 14  # old style numbers still count
    assert download.renumber_existing(jobs) == 2
    assert sorted(f.name for f in tmp_path.iterdir()) == ["2 Tarik.mp4", "3 Jynxzi.mp4"]


def test_a_name_held_by_another_clip_is_not_overwritten(tmp_path):
    from clipdl import download
    other = tmp_path / "1 Tarik.mp4"                  # a clip that left the ranking
    other.write_bytes(b"clip")
    job, = download.build_jobs([_clip("new", "Tarik")], tmp_path, "VALORANT",
                               manifest=_History({"gone": other}))
    assert job.existing is None and job.path.name == "1 Tarik (2).mp4"
