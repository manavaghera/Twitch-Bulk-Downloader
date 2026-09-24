"""Wikipedia page views: how many people read a game's article, week on week.

Read from Wikimedia's page view service, one article per request, four at a
time. That service does not follow redirects, so each title is first turned
into the article it lands on (a light MediaWiki query per 50 titles). Views of
finished days never change, so what was read today is kept in
data/wiki_views.json and a second run the same day asks for nothing.
"""

import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .config import DATA_DIR
from .util import chunked, load_json, save_json, thread_pool

WIKI_VIEWS_FILE = DATA_DIR / "wiki_views.json"      # page views read today


PAGEVIEWS = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
             "%s.wikipedia/all-access/user/%s/daily/%s/%s")
VIEW_WORKERS = 4                # ~25 articles a second; ten at once gets throttled
VIEW_BUDGET = 90                # seconds; what is not read by then waits for the next run


def wiki_canonical(web, language, titles):
    """{title as asked: the article it lands on after redirects, or None when there
    is no such article}. A light request per 50 titles."""
    found = {}
    for batch in chunked(sorted(set(titles)), 50):
        payload = web.get_json("https://%s.wikipedia.org/w/api.php" % language, {
            "action": "query", "titles": "|".join(batch), "redirects": 1, "format": "json",
            "formatversion": 2})
        if payload is None:
            continue                        # unknown this time: asked again next run
        query = payload.get("query") or {}
        asked = {title: title for title in batch}
        for step in (query.get("normalized") or []) + (query.get("redirects") or []):
            asked[step.get("to")] = asked.get(step.get("from"), step.get("from"))
        for page in query.get("pages") or []:
            original = asked.get(page.get("title"), page.get("title"))
            found[original] = None if page.get("missing") or page.get("invalid") \
                else page.get("title")
    return found


def wiki_views(web, articles, windows=(7,)):
    """{(lang, title): {days: (last `days` days, the `days` before)}} in page views.

    Read from Wikimedia's page view service, one article per request, ten at a
    time - it allows up to a hundred a second, where the MediaWiki API's
    pageviews property makes a script wait 40-60 seconds after a few requests.
    That service does not follow redirects ("Baldur's Gate III" only points to
    the real article, whose views are wanted), so every title is first turned
    into the article it lands on. The window ends yesterday, the last full day
    published; the longest is 30 days against the 30 before.
    """
    longest = min(max(windows), 30)
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    first = yesterday - timedelta(days=2 * longest - 1)
    days = [(yesterday - timedelta(days=offset)).isoformat() for offset in range(2 * longest)]
    spans = {n: (set(days[:n]), set(days[n:2 * n])) for n in windows if n <= longest}

    # Views of finished days never change: what was read today is kept, so a
    # second run the same day (or the Autopilot's) asks for nothing again.
    cache = load_json(WIKI_VIEWS_FILE, {})
    key = "%s|%s" % (yesterday.isoformat(), ",".join(str(n) for n in sorted(spans)))
    fresh = isinstance(cache, dict) and cache.get("key") == key
    known = (cache.get("views") or {}) if fresh else {}
    results, by_language = {}, {}
    for language, title in articles:
        stored = known.get("%s|%s" % (language, title))
        if stored is not None:
            if stored:
                results[(language, title)] = {int(n): tuple(v) for n, v in stored.items()}
            continue
        by_language.setdefault(language, []).append(title)
    if not by_language:
        return results

    local = threading.local()
    deadline = time.time() + VIEW_BUDGET

    def client():
        if not hasattr(local, "web"):
            local.web = type(web)(stop=web.stop)   # the caller's kind of client
            local.web.session.headers.update(web.session.headers)
        return local.web

    def canonical(item):
        language, titles = item
        return language, wiki_canonical(client(), language, titles)

    def views_of(job):
        language, title, article = job
        if time.time() > deadline:          # Wikipedia is slow today: carry on without
            return job, None
        payload = client().get_json(PAGEVIEWS % (
            language, quote(article.replace(" ", "_"), safe=""), first.strftime("%Y%m%d"),
            yesterday.strftime("%Y%m%d")))
        if payload is None:
            return job, None
        daily = {"%s-%s-%s" % (i["timestamp"][:4], i["timestamp"][4:6], i["timestamp"][6:8]):
                 i.get("views") or 0 for i in payload.get("items") or [] if i.get("timestamp")}
        return job, {n: _window_sums(daily, recent_days, older_days)
                     for n, (recent_days, older_days) in spans.items()}

    with thread_pool(VIEW_WORKERS) as pool:
        jobs = []
        for language, found in pool.map(canonical, by_language.items()):
            for title in by_language[language]:
                if title not in found:
                    continue                    # could not look it up: next run
                if found[title] is None:
                    known["%s|%s" % (language, title)] = {}     # no such article
                else:
                    jobs.append((language, title, found[title]))
        for (language, title, _article), windows_read in pool.map(views_of, jobs):
            if windows_read is None:
                continue                        # not answered: asked again next run
            if any(recent or older for recent, older in windows_read.values()):
                results[(language, title)] = windows_read
            known["%s|%s" % (language, title)] = {str(n): list(v)
                                                   for n, v in windows_read.items()}
    save_json(WIKI_VIEWS_FILE, {"key": key, "views": known})
    return results


def _window_sums(views, recent_days, older_days):
    recent = sum(v or 0 for d, v in views.items() if d in recent_days)
    # An article younger than the older window cannot be compared.
    had_older = any(v is not None for d, v in views.items() if d in older_days)
    older = sum(v or 0 for d, v in views.items() if d in older_days)
    return recent, older if had_older else 0
