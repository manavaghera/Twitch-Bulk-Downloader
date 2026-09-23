"""One game as seen in one pass over the live streams: the counting the stats
collector does per game - viewers, channels, languages, the biggest channels,
the viewer spread, tags and title words, and Drops."""

import re

TOP_CHANNELS = 10           # biggest channels kept per game per snapshot

# Channel-size buckets for the viewer spread: [1], [2], [3-4], [5-9] ... [1000+].
# Enough to say where a channel with N viewers would sit in a game's directory.
HIST_EDGES = (0, 1, 2, 3, 5, 10, 25, 50, 100, 250, 1000)
TERMS_KEPT = 12             # tags and title words kept per game
TITLE_STOPWORDS = set("""
the and for with you your are this that from have has just now new day live stream streaming
streams today tonight come chill chilling playing play game games gaming first time lets let
more some then than what when where who why how all out get got can will not but its
our their they his her him she was were been being into over under again back going here
there yes very much many one two three road late night morning vibes vibe hang hanging
feat part episode season lol lmao omg
""".split())
# Tags Twitch adds by itself (the stream's language), which say nothing about a game.
LANGUAGE_TAGS = {t.lower() for t in """
English Español Português Français Deutsch Italiano Polski Русский Türkçe 日本語 한국어 中文
العربية ไทย Nederlands Svenska Norsk Dansk Suomi Čeština Magyar Română Ελληνικά Українська
Filipino हिन्दी Català Български Slovenčina 繁體中文 简体中文 עברית Tagalog Indonesia
TiếngViệt BahasaIndonesia BahasaMelayu Other
""".split()}


class Bucket:
    """Everything seen of one game in one pass."""

    def __init__(self, name):
        self.name = name
        self.ids = {}
        self.viewers = []                   # one entry per live channel
        self.langs = {}                     # code -> [viewers, channels]
        self.top = []                       # the biggest channels, as stored
        self.terms = {}                     # "#Tag" or "word" -> [channels, viewers]
        self.drops = 0                      # viewers of streams advertising Drops

    def add(self, viewers, lang, channel_row, tags=(), title=""):
        self.viewers.append(viewers)
        entry = self.langs.setdefault(lang or "other", [0, 0])
        entry[0] += viewers
        entry[1] += 1
        if len(self.top) < TOP_CHANNELS:    # streams arrive biggest first
            self.top.append(channel_row)
        words = {w for w in re.findall(r"[a-z0-9]{3,}", (title or "").lower())
                 if w not in TITLE_STOPWORDS and not w.isdigit()}
        terms = {"#" + str(t) for t in tags or () if t} | words
        for term in terms:
            count = self.terms.setdefault(term, [0, 0])
            count[0] += 1
            count[1] += viewers
        # Twitch tags a stream "DropsEnabled" (in its own language) while a
        # Drops campaign runs on it; a title saying "drops" is the other sign.
        if "drops" in words or any("drop" in str(t).lower() for t in tags or ()):
            self.drops += viewers

    def hist(self):
        """Channels per size bucket, as "n,n,n,..." for HIST_EDGES."""
        counts = [0] * len(HIST_EDGES)
        for v in self.viewers:
            counts[max(i for i, edge in enumerate(HIST_EDGES) if v >= edge)] += 1
        return ",".join(map(str, counts))

    def top_terms(self, own_words):
        """The tags and title words most channels use, minus the game's own name."""
        ranked = sorted(((t, c, v) for t, (c, v) in self.terms.items()
                         if c >= 2 and t.lstrip("#").lower() not in own_words
                         and t.lstrip("#").lower() not in LANGUAGE_TAGS
                         and not (t.startswith("#") and "drop" in t.lower())),
                        key=lambda item: (item[1], item[2]), reverse=True)
        tags = [row for row in ranked if row[0].startswith("#")][:TERMS_KEPT]
        words = [row for row in ranked if not row[0].startswith("#")][:TERMS_KEPT]
        return tags + words

    @property
    def total(self):
        return sum(self.viewers)

    def top_n(self, n):
        return sum(sorted(self.viewers, reverse=True)[:n])


def group(streams, key_of):
    buckets = {}
    for stream in streams:
        found = key_of(stream)
        if not found:
            continue
        key, name, ids, viewers, lang, row = found[:6]
        tags, title = found[6] if len(found) > 6 else ((), "")
        bucket = buckets.setdefault(key, Bucket(name))
        bucket.ids.update({k: v for k, v in ids.items() if v})
        bucket.add(viewers, lang, row, tags, title)
    return buckets
