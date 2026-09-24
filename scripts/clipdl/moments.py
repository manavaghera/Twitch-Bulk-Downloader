"""Turning the signals into moments: one score a second, the best stretches of
it, and clean edges.

  signals     each is judged against its own surroundings (a loud game stays
              loud; fewer people watch hour three) and ranked 0..1, so they count
              alike - except kills, used as they are: a lone kill must not look
              like an ace just because kills are rare
  the gate    seconds where the picture hardly changes (menus, lobbies, talking
              to camera) keep only part of their score
  stretches   the best non-overlapping stretches of the chosen length, the
              action a little before the middle (the lead-up, then the reaction)
  edges       moved to the nearest pause in the talking, so a clip never starts
              or ends in the middle of a sentence
"""

WEIGHTS = {"kills": 1.0, "replayed": 0.45, "chat": 0.35, "hype": 0.5, "action": 0.15,
           "loud": 0.05}
AS_THEY_ARE = {"kills"}         # already 0..1 and comparable between videos
GATE = 0.55                     # a second with no movement on screen keeps this much
SKIP_START = 60                 # "starting soon" screens and intros are never a highlight
SKIP_END = 45                   # nor are end screens and "thanks for watching"
FOCUS = 0.45                    # where in the clip the action sits (0 start .. 1 end)
PAUSE = 0.3                     # seconds of silence that count as a pause
REACH = 3.0                     # how far an edge may move to find a pause


def smooth(values, width):
    """A running average over `width` seconds (a burst, not a single blip)."""
    out, total, window = [], 0.0, []
    for value in values:
        window.append(value)
        total += value
        if len(window) > width:
            total -= window.pop(0)
        out.append(total / len(window))
    return out


def bumps(values, seconds=None):
    """How far each second stands above its own surroundings (the few minutes around
    it). Raw levels drift - fewer people watch hour three, a loud game stays loud -
    so a highlight is a bump, not a high level."""
    count = len(values)
    window = int(min(max((seconds or count) / 8, 60), 600))
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)
    out = []
    for t in range(count):
        low, high = max(0, t - window // 2), min(count, t + window // 2 + 1)
        baseline = (prefix[high] - prefix[low]) / (high - low)
        out.append(values[t] - baseline)
    return out


def ranked(values):
    """Each value's rank among all of them, 0..1 - so every signal counts alike.
    Equal values share one rank (a flat stretch must not favour its later seconds)."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    top = max(len(values) - 1, 1)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for position in range(i, j + 1):
            ranks[order[position]] = (i + j) / 2.0 / top
        i = j + 1
    return ranks


def prepared(curves, seconds):
    """{name: 0..1 a second} for the signals there are."""
    return {name: (list(curve) if name in AS_THEY_ARE else ranked(bumps(curve, seconds)))
            for name, curve in curves.items() if curve}


def blend(curves, seconds, gate=None):
    """{name: curve or None} -> one score a second, 0..1, weighted. `gate`
    (movement on screen) turns down the seconds where the picture hardly changes."""
    present = prepared(curves, seconds)
    if not present:
        return [0.0] * seconds
    weight = sum(WEIGHTS[name] for name in present)
    score = [sum(WEIGHTS[name] * curve[t] for name, curve in present.items()) / weight
             for t in range(seconds)]
    if gate:
        moving = ranked(gate)
        score = [value * (GATE + (1 - GATE) * moving[t]) for t, value in enumerate(score)]
    return score


def pick(score, count, length, gap=10, skip_start=SKIP_START, skip_end=SKIP_END):
    """The `count` best non-overlapping stretches: [(start, end, how good 0..1)].
    Seconds near FOCUS count a little more, so a stretch is placed with its best
    part just before the middle rather than anywhere it happens to fit."""
    seconds = len(score)
    if seconds <= length:
        return [(0, seconds, 1.0)]
    focus = length * FOCUS
    weights = [1.0 + 0.25 * (1 - abs(k - focus) / max(focus, length - focus))
               for k in range(length)]
    total = sum(weights)
    # Intros and end screens are left out - unless the video is too short to spare them.
    first = min(skip_start, max(seconds - length - 1, 0))
    last = max(seconds - length - min(skip_end, max(seconds - length - first, 0)), first)
    windows = []
    for t in range(first, last + 1, 2):
        windows.append((sum(w * score[t + k] for k, w in enumerate(weights)) / total, t))
    windows.sort()
    chosen = []
    for value, start in reversed(windows):
        if all(abs(start - other) >= length + gap for other, _e, _v in chosen):
            chosen.append((start, start + length, value))
        if len(chosen) == count:
            break
    return chosen


def snap(start, end, words, seconds, reach=REACH):
    """(start, end) moved to pauses in the talking, when there is one within
    `reach` seconds: the start back to where the sentence began, the end on to
    where it finished."""
    if not words:
        return start, end
    begins = [s - 0.2 for i, (s, _e, _w) in enumerate(words)
              if i == 0 or s - words[i - 1][1] >= PAUSE]
    ends = [e + 0.2 for i, (_s, e, _w) in enumerate(words)
            if i == len(words) - 1 or words[i + 1][0] - e >= PAUSE]

    def talking(t):
        return any(s - 0.05 < t < e + 0.05 for s, e, _w in words)
    if talking(start):
        before = [b for b in begins if start - reach <= b <= start]
        if before:
            start = before[-1]
    if talking(end):
        after = [e for e in ends if end <= e <= end + reach]
        if after:
            end = after[0]
    return int(max(start, 0)), int(min(round(end + 0.49), seconds))
