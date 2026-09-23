from clipdl import permissions, radar, session
from clipdl.filter import classify_clip


def test_spam_catches_site_and_cheat_ads():
    assert radar.is_spam({"title": "free skins at cheapskins.su"})
    assert radar.is_spam({"title": "best CHEATS here"})
    assert radar.is_spam({"title": "undetected aimbot 👉 link"})
    assert not radar.is_spam({"title": "insane ace on ascent"})


def test_spam_needs_whole_words():
    assert not radar.is_spam({"title": "cheatsheet for the new map"})
    assert not radar.is_spam({"title": "he said .com is dead lol"})
    assert not radar.is_spam({"title": "hacks the vault in the heist"})


def test_talking_clips_are_recognised():
    assert classify_clip({"title": "just chatting with chat"}, "")[0] == "talk"
    assert classify_clip({"title": "INSANE 1v5 ACE"}, "")[0] == "gameplay"


def test_stream_title_can_tip_the_verdict():
    kind, _ = classify_clip({"title": "lmao"}, "watch party - reacting to the finals")
    assert kind in ("talk", "maybe")


def test_keep_gameplay_drops_only_talk(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "VOD_FILE", tmp_path / "vod.json")
    clips = [{"id": "1", "title": "just chatting with chat"},
             {"id": "2", "title": "INSANE 1v5 ACE"},
             {"id": "3", "title": "caster desk reacts"}]
    assert [c["id"] for c in session.keep_gameplay(clips)] == ["2"]


def test_radar_filter_hides_talk_and_spam():
    clips = [{"title": "INSANE ACE", "spam": False, "language": "en", "view_count": 900,
              "have": False, "permission": "unknown"},
             {"title": "just chatting with chat", "spam": False, "language": "en",
              "view_count": 900, "have": False, "permission": "unknown"},
             {"title": "go to cheapskins.su", "spam": True, "language": "en",
              "view_count": 900, "have": False, "permission": "unknown"}]
    shown = radar.filtered({"clips": clips}, "en", 500)
    assert [c["title"] for c in shown] == ["INSANE ACE"]
    assert len(radar.filtered({"clips": clips}, "en", 500, hide_talk=False,
                              hide_spam=False)) == 3


def test_permission_modes(tmp_data):
    permissions.set_status("Good", "allowed", user_id="1")
    permissions.set_status("Bad", "blocked", user_id="2")
    good = {"broadcaster_id": "1", "broadcaster_name": "Good"}
    bad = {"broadcaster_id": "2", "broadcaster_name": "Bad"}
    other = {"broadcaster_id": "3", "broadcaster_name": "Other"}
    assert permissions.allow_filter("any") is None
    not_blocked = permissions.allow_filter("not_blocked")
    assert not_blocked(good) and not_blocked(other) and not not_blocked(bad)
    allowed_only = permissions.allow_filter("allowed_only")
    assert allowed_only(good) and not allowed_only(other) and not allowed_only(bad)
