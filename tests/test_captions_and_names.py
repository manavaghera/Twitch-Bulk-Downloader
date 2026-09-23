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
