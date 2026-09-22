"""Which languages stand in for which region, and what a view is worth there.

TWO HONEST WARNINGS, because both matter when reading anything built on this
file:

1. Twitch's API reports the LANGUAGE a stream is broadcast in. It does not
   report where the viewers are. There is no Helix endpoint that does. So a
   "US share" here is really "English-language share", which also picks up the
   UK, Canada, Australia and a good part of Scandinavia watching in English.
   Treat it as a strong hint about a game's centre of gravity, not as a
   headcount.

2. The RPM numbers below are PUBLISHED INDUSTRY ESTIMATES that were typed into
   this file by hand, not live data pulled from YouTube. Nothing in this repo
   can read your actual earnings - only YouTube Analytics knows those. They are
   ranges because real RPM swings with the month, the advertiser season, the
   video length and the niche. Use them to rank countries against each other,
   never as a forecast of what you will be paid.
"""

# Languages that stand in for "Europe", excluding English, which is counted on
# its own because it spans the US, the UK and a lot of European viewers too.
EUROPEAN_LANGUAGES = {
    "de": "German", "fr": "French", "it": "Italian",
    "pl": "Polish", "nl": "Dutch", "sv": "Swedish", "no": "Norwegian",
    "da": "Danish", "fi": "Finnish", "cs": "Czech",
    "hu": "Hungarian", "ro": "Romanian", "el": "Greek", "bg": "Bulgarian",
    "sk": "Slovak", "hr": "Croatian", "sr": "Serbian", "uk": "Ukrainian",
    "ca": "Catalan", "sl": "Slovenian", "lt": "Lithuanian", "lv": "Latvian",
    "et": "Estonian",
}

# Twitch uses ONE code for both Portugals and both Spanishes, and on Twitch the
# overwhelming majority of each is Brazil and Latin America, not Iberia. Adding
# them to the set above would quietly turn a huge Brazilian audience into a
# "European" one and push those games up a ranking meant for US/EU reach, so
# they are counted on their own and shown separately instead.
AMBIGUOUS_LANGUAGES = {"pt": "Portuguese (mostly Brazil)",
                       "es": "Spanish (mostly LatAm)"}

# Large, partly-European audiences that most people do not mean by "Europe".
BORDERLINE_LANGUAGES = {"ru": "Russian", "tr": "Turkish"}


# ---------------------------------------------------------------------------
# RPM reference table
#
# RPM = what you actually keep per 1000 views, after YouTube's cut and after
# unmonetised views are counted. It is NOT CPM (what advertisers pay).
#
# Figures are typical LONG-FORM GAMING/ENTERTAINMENT ranges in USD, gathered
# from public creator reporting and ad-network summaries. Gaming sits below the
# site-wide average; finance and business sit far above it.
#
# Source date: 2026-09. Re-check these before making any real decision on them.
# Edit this table freely - nothing else has to change.
# ---------------------------------------------------------------------------
RPM_TABLE = [
    # (country, language code it usually maps to, low USD, high USD)
    ("Norway",          "no", 8.00, 14.00),
    ("Australia",       "en", 7.50, 12.00),
    ("Denmark",         "da", 7.00, 12.00),
    ("Switzerland",     "de", 7.00, 11.50),
    ("United States",   "en", 6.50, 11.00),
    ("Sweden",          "sv", 6.00, 10.50),
    ("Netherlands",     "nl", 6.00, 10.00),
    ("Canada",          "en", 5.50,  9.00),
    ("United Kingdom",  "en", 5.50,  9.00),
    ("Germany",         "de", 5.00,  9.00),
    ("New Zealand",     "en", 5.00,  8.50),
    ("Austria",         "de", 5.00,  8.50),
    ("Finland",         "fi", 4.50,  8.00),
    ("Ireland",         "en", 4.50,  8.00),
    ("Belgium",         "nl", 4.00,  7.00),
    ("France",          "fr", 3.50,  6.50),
    ("Japan",           "ja", 3.50,  6.50),
    ("South Korea",     "ko", 2.50,  5.00),
    ("Italy",           "it", 2.00,  4.00),
    ("Spain",           "es", 2.00,  4.00),
    ("Czechia",         "cs", 1.50,  3.00),
    ("Poland",          "pl", 1.50,  3.00),
    ("Portugal",        "pt", 1.50,  3.00),
    ("Greece",          "el", 1.20,  2.50),
    ("Romania",         "ro", 1.00,  2.20),
    ("Mexico",          "es", 0.80,  1.80),
    ("Brazil",          "pt", 0.70,  1.50),
    ("Turkey",          "tr", 0.50,  1.20),
    ("Russia",          "ru", 0.40,  1.20),
    ("India",           "hi", 0.40,  1.10),
    ("Indonesia",       "id", 0.30,  0.80),
    ("Philippines",     "tl", 0.30,  0.80),
]

# What a language is worth on average, used to turn an audience mix into a
# blended estimate. Built from the table above so the two never drift apart.
def _language_averages():
    totals = {}
    for _country, code, low, high in RPM_TABLE:
        totals.setdefault(code, []).append((low + high) / 2.0)
    return {code: sum(values) / len(values) for code, values in totals.items()}


LANGUAGE_RPM = _language_averages()

# Roughly what the rest of the world pays, for languages missing from the
# table. Deliberately low: the countries not listed are mostly low-RPM markets.
FALLBACK_RPM = 1.00

# Shorts are paid from a completely different pool and land far below long-form.
# Anyone cutting Twitch clips is very likely posting Shorts, so the report says
# this out loud rather than letting the long-form numbers mislead.
SHORTS_RPM_LOW = 0.04
SHORTS_RPM_HIGH = 0.18


def classify_language(code):
    """'english', 'europe', 'ambiguous', 'borderline' or 'other' for a code."""
    code = (code or "").lower()
    if code == "en":
        return "english"
    if code in EUROPEAN_LANGUAGES:
        return "europe"
    if code in AMBIGUOUS_LANGUAGES:
        return "ambiguous"
    if code in BORDERLINE_LANGUAGES:
        return "borderline"
    return "other"


def language_name(code):
    code = (code or "").lower()
    if code == "en":
        return "English"
    return (EUROPEAN_LANGUAGES.get(code) or AMBIGUOUS_LANGUAGES.get(code)
            or BORDERLINE_LANGUAGES.get(code) or code.upper() or "unknown")


def blended_rpm(viewers_by_language):
    """Turn {language: viewers} into an estimated USD RPM for that audience mix.

    A game watched mostly in English is worth several times one watched mostly
    in Portuguese, which is the whole reason this is worth computing.
    """
    total = sum(viewers_by_language.values())
    if total <= 0:
        return 0.0
    paid = sum(LANGUAGE_RPM.get(code, FALLBACK_RPM) * count
               for code, count in viewers_by_language.items())
    return paid / total


def best_rpm_countries(limit=15):
    """The RPM table, richest first."""
    return sorted(RPM_TABLE, key=lambda row: (row[2] + row[3]) / 2.0, reverse=True)[:limit]
