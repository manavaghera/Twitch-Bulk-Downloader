from clipdl import trends
from clipdl.trends import GameTrend, rank_trends


def game(name, viewers=0, clips=0, wiki=0, streams_id="1"):
    trend = GameTrend(name)
    trend.id = streams_id
    trend.live_viewers = viewers
    trend.viewers_by_language = {"en": viewers or 1}
    trend.recent_views = clips
    trend.previous_views = clips
    if wiki:
        trend.wiki_titles = {"en": name}
        trend.wiki_recent = trend.wiki_previous = wiki
    return trend


def test_log_share_is_scaled_to_the_biggest():
    assert trends._log_share(0, 100) == 0.0
    assert trends._log_share(100, 100) == 1.0
    assert 0 < trends._log_share(10, 100) < trends._log_share(50, 100) < 1


def test_bigger_game_ranks_higher():
    big, small = game("Big", 90000, 5_000_000, 400_000), game("Small", 900, 20_000, 4_000)
    popular, _rising = rank_trends([small, big])
    assert [t.name for t in popular] == ["Big", "Small"]
    assert popular[0].popularity == 1.0 and 0 < popular[1].popularity < 1


def test_fewer_sources_cost_a_little():
    everywhere = game("Everywhere", 5000, 100_000, 50_000)
    twitch_only = game("TwitchOnly", 5000, 100_000)
    rank_trends([everywhere, twitch_only])
    assert everywhere.evidence > twitch_only.evidence
    assert everywhere.popularity > twitch_only.popularity


def test_same_article_listed_once():
    a, b = game("GTA V", 50000, 10, 100), game("GTA V Enhanced", 40000, 10, 100)
    b.wiki_titles = dict(a.wiki_titles)
    popular, _ = rank_trends([a, b])
    assert [t.name for t in popular] == ["GTA V"]


def test_heat_is_bounded():
    assert trends._heat(float("inf")) == 1.0
    assert trends._heat(10.0) == 1.0
    assert trends._heat(-5.0) == -1.0
    assert trends._heat(0.0) == 0.0
