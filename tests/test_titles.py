from clipdl import titles


def test_login_from_new_style_clip_url():
    assert titles.login_from("https://www.twitch.tv/Tarik/clip/Slug-abc", "Tarik") == "tarik"


def test_login_from_plain_display_name():
    assert titles.login_from("https://clips.twitch.tv/FunnySlug", "Shroud") == "shroud"


def test_login_unknown_for_names_in_another_script():
    assert titles.login_from("https://clips.twitch.tv/X", "한동숙") == ""
    assert titles.login_from("", "Ninja한국") == ""        # never a half-stripped wrong link


def test_description_links_the_right_channel():
    out = titles.suggest("insane ace", "한동숙", "VALORANT", "valorant",
                         "https://clips.twitch.tv/X", login="handongsuk")
    assert "twitch.tv/handongsuk" in out["description"]
    assert "Original clip: https://clips.twitch.tv/X" in out["description"]


def test_no_channel_link_when_login_unknown():
    out = titles.suggest("insane ace", "한동숙", "VALORANT", "valorant", "https://clips.twitch.tv/X")
    assert ": twitch.tv/" not in out["description"]


def test_three_titles_crediting_the_streamer():
    out = titles.suggest("CRAZY 1v4 clutch", "Tarik", "VALORANT", "valorant", "u")
    assert len(out["titles"]) == 3
    assert all("Tarik" in t for t in out["titles"])
    assert all(len(t) <= 95 for t in out["titles"])


def test_hashtags_are_clean_and_capped():
    out = titles.suggest("gg", "Some_One", "Counter-Strike 2", "counterstrike2", "u",
                         trending=["Fun!", "fps", "a b", "x", "y", "z", "w", "v", "q"])
    assert len(out["hashtags"]) <= 9
    assert all(t.startswith("#") and t[1:].isalnum() or "_" in t for t in out["hashtags"])
    assert "#some_one" in out["hashtags"]


def test_hooks_match_whole_words_only():
    # "window" must not count as "win".
    a = titles.suggest("look at this window", "A", "Game", "game", "same-url")
    b = titles.suggest("look at this doorway", "A", "Game", "game", "same-url")
    assert a["titles"][1] == b["titles"][1]


def test_same_clip_gives_the_same_suggestion():
    first = titles.suggest("nice shot", "A", "Game", "game", "https://clips.twitch.tv/Z")
    assert first == titles.suggest("nice shot", "A", "Game", "game", "https://clips.twitch.tv/Z")


def test_sidecar_text_has_all_sections():
    text = titles.sidecar_text(titles.suggest("wow", "A", "Game", "game", "u"))
    for heading in ("TITLE OPTIONS", "DESCRIPTION", "HASHTAGS"):
        assert heading in text
