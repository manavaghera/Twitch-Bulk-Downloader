"""A second opinion on each candidate moment, from an AI model on this computer.

The signals find where something happens; they cannot tell a joke from a rant.
A local language model (Ollama, https://ollama.com - free, nothing leaves the
computer) reads what was said in each candidate stretch - with what the screen
showed, e.g. "3 kills" - and rates it 0..10: a clutch or big play with a real
reaction, or something genuinely funny, scores high; callouts, planning, small
talk and the game's announcer alone score low. Without Ollama running, the
moments are picked without it.
"""

import json
import re

import requests

from .util import say

ADDRESS = "http://127.0.0.1:11434"
PREFERRED = ("llama3", "llama3.1", "llama3.2", "qwen2.5", "gemma", "mistral", "phi")
SYSTEM = ("You pick highlight clips from gaming videos and live streams for YouTube Shorts. "
          "You get what was said in a stretch of about 30 seconds (speech-to-text, so it has "
          "mistakes; lines like 'one enemy remaining' or 'spike planted' are the game's "
          "announcer) and, when known, what happened on screen. Rate how good a Short it "
          "makes: a clutch or big play with a real reaction, or a genuinely funny moment, "
          "rates high; ordinary callouts, planning, small talk and announcer lines alone rate "
          "low. Answer only JSON: {\"score\": 0-10, \"kind\": \"clutch\"|\"funny\"|\"hype\"|"
          "\"boring\", \"why\": \"under 12 words\"}")


def model():
    """The name of a chat model Ollama has, or None when Ollama is not running."""
    try:
        tags = requests.get(ADDRESS + "/api/tags", timeout=2).json().get("models") or []
    except (requests.RequestException, ValueError):
        return None
    names = [t.get("name") or "" for t in tags
             if "embed" not in (t.get("name") or "") and "coder" not in (t.get("name") or "")]
    for want in PREFERRED:
        for name in names:
            if name.split(":")[0] == want:
                return name
    return names[0] if names else None


def rate(text, context="", name=None, timeout=90):
    """(score 0..1, kind, why) for one stretch, or None when the model cannot say."""
    name = name or model()
    if not name or not (text.strip() or context):
        return None
    ask = ("On screen: %s\n" % context if context else "") + "Said: " + (
        text.strip()[:1500] or "(nothing)")
    try:
        reply = requests.post(ADDRESS + "/api/chat", timeout=timeout, json={
            "model": name, "stream": False, "format": "json",
            "options": {"temperature": 0, "num_predict": 80},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": ask}]}).json()
        answer = json.loads(reply["message"]["content"])
        score = float(answer.get("score"))
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None
    kind = str(answer.get("kind") or "").lower()
    why = re.sub(r"\s+", " ", str(answer.get("why") or "")).strip()[:80]
    return max(0.0, min(score, 10.0)) / 10.0, kind, why


def rate_all(stretches, name=None):
    """[(score, kind, why) or None] for [(text, context)] - one at a time (the
    model uses the graphics card), each labelled as it goes."""
    name = name or model()
    if not name:
        return [None] * len(stretches)
    say("A second opinion from %s on %d moment(s)..." % (name, len(stretches)))
    out = []
    for number, (text, context) in enumerate(stretches, 1):
        out.append(rate(text, context, name))
        say("[%d/%d] Judging moments" % (number, len(stretches)))
    return out
