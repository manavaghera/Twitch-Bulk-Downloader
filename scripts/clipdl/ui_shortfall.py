"""The Clip downloader's "search first, then ask" step on the web page.

The download button runs the search as a background job. When it finds all
the clips asked for, the download carries straight on. When it comes up
short, the job ends with a Plan (see shortfall.py) and nothing downloaded;
this card shows what was found, every way to fill the gap with how many each
adds, and downloads only once you pick.
"""

import time

import streamlit as st

from . import shortfall, timing, ui_theme
from .config import STOP
from .session import DownloadResult, build_zip, download_plan, ffmpeg_ready
from .ui_common import start_job, usual_time
from .util import say


def search_then_download(api, request, as_zip):
    """The download job: search, and download at once unless it came up short."""
    if not ffmpeg_ready(request):
        return DownloadResult(1, request=request)
    started = time.monotonic()
    plan = shortfall.find(api, request)
    timing.record("search", time.monotonic() - started)
    if plan is None:
        return DownloadResult(1, request=request)
    plan.as_zip = as_zip
    if plan.missing and plan.options():
        say("")
        say("Nothing downloaded yet - choose on the page how to fill the other %d."
            % plan.missing)
        return plan
    return finish(api, plan, ())


def finish(api, plan, fills):
    result = download_plan(api, plan, fills)
    if getattr(plan, "as_zip", False) and result.code == 0 and not STOP.is_set():
        build_zip(result)
    return result


def waiting_plan(job):
    """The Plan a finished search left for the page to ask about, or None."""
    if (job is None or job.running or not isinstance(job.result, shortfall.Plan)
            or st.session_state.get("dl_plan_closed") == job.started):
        return None
    return job.result


def card(api, job):
    """Found 300 of 1,000: how to fill the rest, then download."""
    plan = job.result
    request = plan.request
    with st.container(border=True, key="card_shortfall"):
        ui_theme.step("🔎", "Found %s of %s clips" % ("{:,}".format(len(plan.base)),
                                                     "{:,}".format(request.wanted)),
                      "Nothing is downloaded yet. With your rules (%s) there are %s fewer "
                      "than you asked for - choose how to fill the gap, or take what was "
                      "found." % (" · ".join(plan.summary()), "{:,}".format(plan.missing)))
        fills = []
        for key, label, count in plan.options():
            if st.checkbox("%s  (+%s)" % (label, "{:,}".format(count)), key="dl_fill_%s" % key,
                           value=key == "older"):
                fills.append(key)
        total = len(plan.choose(fills, quiet=True))
        ui_theme.note("That makes <b>%s</b> of the %s you asked for. Quality never limits the "
                      "count: every clip comes down at the best it has, up to your cap."
                      % ("{:,}".format(total), "{:,}".format(request.wanted)))
        estimate = timing.download_run(total, request.output, request.captions, searches=0)
        cols = st.columns([2, 1])
        go = cols[0].button("Download %s clip%s" % ("{:,}".format(total),
                                                    "" if total == 1 else "s"),
                            type="primary", icon=":material/download:", width="stretch",
                            disabled=not total, key="dl_fill_go")
        if cols[1].button("Cancel", icon=":material/close:", width="stretch",
                          key="dl_fill_cancel"):
            st.session_state["dl_plan_closed"] = job.started
            st.rerun()
        usual_time(estimate)
    if go:
        st.session_state["dl_plan_closed"] = job.started
        start_job("download", "%s, %d clips" % (request.game_name, total),
                  lambda: finish(api, plan, fills), estimate)


def closed_note(job):
    """Shown once the card was closed without downloading."""
    if (job is not None and not job.running and isinstance(job.result, shortfall.Plan)
            and st.session_state.get("dl_plan_closed") == job.started):
        st.info("Nothing was downloaded. Change the settings above and search again.",
                icon=":material/info:")
        return True
    return False
