"""Burned-in captions for Shorts: speech to text on this PC, no API, no cost.

faster-whisper turns speech into words with the time each was said; they are
grouped into short lines and written as an .ass subtitle file that ffmpeg
burns into the video.

ACCURACY (data/captions.json, "model")
  fast     base      ~150 MB, quick on any PC
  better   small     ~480 MB, clearly fewer mistakes
  best     large-v3-turbo  ~1.6 GB, close to human - meant for an NVIDIA card
  auto     "best" when the graphics card can run it, else "better"
Models download once, on first use. The graphics card needs NVIDIA's cuBLAS
and cuDNN libraries (pip install nvidia-cublas-cu12 nvidia-cudnn-cu12); without
them everything runs on the processor.

LOOKS
  pop      the line on screen, the word being said lit up and a little bigger
  karaoke  the colour sweeps through the line as it is said
  classic  plain white words with a black outline
  boxed    white words on a dark box
Two or three words at a time, broken at pauses and punctuation, in the lower
third (above the buttons Shorts and TikTok put at the bottom), the middle or
the top.
"""

import glob
import os
import sys
import threading

from .config import DATA_DIR
from .util import load_json, save_json

SETTINGS = DATA_DIR / "captions.json"
DEFAULTS = {"model": "auto", "language": "en", "style": "pop", "position": "lower",
            "size": "m", "color": "yellow", "caps": True}
MODELS = {"fast": "base", "better": "small", "best": "large-v3-turbo"}
STYLES = {"pop": "Pop - the word being said lights up", "karaoke": "Karaoke - colour sweeps",
          "classic": "Classic - white", "boxed": "Boxed - white on a dark box"}
POSITIONS = {"lower": "Lower third", "middle": "Middle", "top": "Top"}
SIZES = {"s": 70, "m": 86, "l": 104}
COLORS = {"yellow": "&H0000F0FF", "green": "&H0033FF55", "cyan": "&H00FFE000",
          "red": "&H004040FF", "purple": "&H00FF70A9"}      # ASS colours are BGR
WORDS_PER_LINE = 3
MAX_CHARS = 18
MAX_SECONDS = 1.6
MAX_WORD_SECONDS = 0.9
PAUSE = 0.45                    # a gap this long starts a new line

GPU_ERRORS = ("cuda", "cudnn", "cublas", ".dll", ".so")   # GPU libraries missing
_models = {}
_model_lock = threading.Lock()
_run_lock = threading.Lock()    # one transcription at a time: the model is shared
_gpu_failed = False

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial Black,{size},&H00FFFFFF,&H000000FF,&H00000000,&H78000000,-1,0,0,0,100,100,0,0,1,7,3,2,70,70,{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def settings():
    data = load_json(SETTINGS, {})
    config = dict(DEFAULTS, **(data if isinstance(data, dict) else {}))
    for key, allowed in (("style", STYLES), ("position", POSITIONS), ("size", SIZES),
                         ("color", COLORS)):
        if config[key] not in allowed:
            config[key] = DEFAULTS[key]
    return config


def save_settings(**changes):
    save_json(SETTINGS, dict(settings(), **changes))


# -- the speech model ----------------------------------------------------------------------
def available():
    """True when faster-whisper is installed."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _nvidia_libraries():
    """Let Windows find cuBLAS and cuDNN when they came as pip packages."""
    if sys.platform != "win32":
        return
    import site
    roots = site.getsitepackages() + [site.getusersitepackages()]
    for root in roots:
        for folder in glob.glob(os.path.join(root, "nvidia", "*", "bin")):
            if folder not in os.environ.get("PATH", ""):
                os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
                try:
                    os.add_dll_directory(folder)
                except OSError:
                    pass


def gpu_ready():
    """An NVIDIA card AND the CUDA libraries faster-whisper needs (cuBLAS, cuDNN).
    Without the libraries a GPU model loads fine and only fails - or hangs - when
    used, so they are looked for up front."""
    if _gpu_failed:
        return False
    _nvidia_libraries()
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() < 1:
            return False
    except Exception:
        return False
    if sys.platform == "win32":
        import ctypes
        for library in ("cublas64_12.dll", "cudnn_ops64_9.dll"):
            try:
                ctypes.WinDLL(library)
            except OSError:
                return False
    return True


def model_name(config=None):
    """The model the settings ask for ("auto": the best this PC runs well)."""
    config = config or settings()
    choice = config["model"]
    if choice not in MODELS:
        choice = "best" if gpu_ready() else "better"
    name = MODELS[choice]
    if config["language"] == "en" and name in ("base", "small"):
        name += ".en"                       # the English-only models are more accurate
    return name


def _load(name):
    with _model_lock:
        key = name
        if key not in _models:
            from faster_whisper import WhisperModel
            model = None
            if gpu_ready():
                try:
                    model = WhisperModel(name, device="cuda", compute_type="float16")
                except Exception:
                    model = None
            if model is None:
                model = WhisperModel(name, device="cpu", compute_type="int8")
            _models[key] = model
        return _models[key]


def transcribe(path, config=None, _retry=True):
    """[(start, end, word)] for the speech in a clip; [] when nothing is said."""
    global _gpu_failed
    config = config or settings()
    name = model_name(config)
    failed = False
    with _run_lock:
        model = _load(name)     # inside the lock: after a switch to the CPU, everyone uses it
        try:
            segments, _info = model.transcribe(
                str(path), word_timestamps=True, vad_filter=True, beam_size=5,
                language=None if config["language"] == "auto" else config["language"],
                condition_on_previous_text=False)
            return [(w.start, w.end, w.word.strip()) for seg in segments
                    for w in (seg.words or []) if w.word.strip()]
        except RuntimeError as error:
            # A GPU without NVIDIA's CUDA libraries only fails here, at the first
            # real use. Switch to the CPU - outside the lock, which the retry takes.
            if not _retry or not any(word in str(error).lower() for word in GPU_ERRORS):
                raise
            failed = True
    if failed:
        with _model_lock:
            _gpu_failed = True
            _models.clear()
        return transcribe(path, config, _retry=False)
    return []


def listen(path, config=None):
    """Every word said in a long recording, as it is heard: yields (start, end, word).
    The fast model, one pass (beam 1): for finding moments, not for captions."""
    config = config or settings()
    name = "base.en" if config["language"] == "en" else "base"
    with _run_lock:
        model = _load(name)
        segments, _info = model.transcribe(
            str(path), word_timestamps=True, vad_filter=True, beam_size=1,
            language=None if config["language"] == "auto" else config["language"],
            condition_on_previous_text=False)
        for segment in segments:
            for word in segment.words or []:
                if word.word.strip():
                    yield word.start, word.end, word.word.strip()


def device():
    """"graphics card" or "processor" - what captions will run on."""
    return "graphics card" if gpu_ready() else "processor"


# -- words into lines ----------------------------------------------------------------------------
def groups(words):
    """The words in short lines: [[(start, end, word), ...], ...]. A line ends at
    three words, 18 characters, 1.6 seconds, a pause, or the end of a sentence."""
    # Whisper sometimes stretches a word over the silence after it; a caption
    # should not hang on screen for seconds after it was said.
    words = [(float(s), min(float(e), float(s) + MAX_WORD_SECONDS), w) for s, e, w in words]
    out, current = [], []
    for start, end, word in words:
        if current:
            text = " ".join(w for _s, _e, w in current + [(start, end, word)])
            if (len(current) >= WORDS_PER_LINE or len(text) > MAX_CHARS
                    or end - current[0][0] > MAX_SECONDS or start - current[-1][1] > PAUSE
                    or current[-1][2][-1:] in ".!?"):
                out.append(current)
                current = []
        current.append((start, end, word))
    if current:
        out.append(current)
    return out


def chunks(words):
    """Caption lines: [(start, end, text)], with no blink-short gaps between them."""
    lines = [(g[0][0], g[-1][1], " ".join(w for _s, _e, w in g)) for g in groups(words)]
    return _smooth(lines)


def _smooth(lines):
    smoothed = []
    for i, (start, end, text) in enumerate(lines):
        if i + 1 < len(lines) and lines[i + 1][0] - end < 0.25:
            end = lines[i + 1][0]           # gaps shorter than a blink make lines flicker
        smoothed.append((start, end, text))
    return smoothed


# -- writing the .ass file --------------------------------------------------------------------------
def _stamp(seconds):
    seconds = max(0.0, seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return "%d:%02d:%05.2f" % (hours, minutes, secs)


def _escape(text, caps=True):
    text = text.replace("\\", "").replace("{", "(").replace("}", ")")
    return text.upper() if caps else text


def _header(config, width, height):
    size = SIZES[config["size"]]
    highlight = COLORS[config["color"]]
    alignment, margin = {"lower": (2, int(height * 0.29)), "middle": (5, 0),
                         "top": (8, int(height * 0.16))}[config["position"]]
    boxed = config["style"] == "boxed"
    primary, secondary = ((highlight, "&H00FFFFFF") if config["style"] == "karaoke"
                          else ("&H00FFFFFF", "&H000000FF"))
    style = ("Style: Cap,Arial Black,%d,%s,%s,&H00000000,%s,-1,0,0,0,100,100,0,0,%d,%d,%d,"
             "%d,60,60,%d,1" % (size, primary, secondary, "&H99000000" if boxed else "&H78000000",
                                3 if boxed else 1, 14 if boxed else 7, 0 if boxed else 3,
                                alignment, margin))
    return ASS_HEADER.split("Style: Cap")[0].format(w=width, h=height) + style + \
        "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, " \
        "Effect, Text\n"


def _events(line_groups, config):
    caps, highlight = config["caps"], COLORS[config["color"]]
    style = config["style"]
    spans = _smooth([(g[0][0], g[-1][1], "") for g in line_groups])
    out = []
    for group, (start, end, _t) in zip(line_groups, spans):
        words = [_escape(w, caps) for _s, _e, w in group]
        if style == "pop":
            # One event per word: the whole line, the word being said lit and bigger.
            for i, (w_start, _w_end, _w) in enumerate(group):
                w_end = group[i + 1][0] if i + 1 < len(group) else end
                text = " ".join(
                    ("{\\c%s\\fscx112\\fscy112}%s{\\r}" % (highlight, word)) if j == i else word
                    for j, word in enumerate(words))
                pop = "{\\fscx90\\fscy90\\t(0,90,\\fscx100\\fscy100)}" if i == 0 else ""
                out.append((max(w_start, start) if i == 0 else w_start, w_end, pop + text))
        elif style == "karaoke":
            parts = []
            for i, (w_start, w_end, _w) in enumerate(group):
                nxt = group[i + 1][0] if i + 1 < len(group) else end
                parts.append("{\\kf%d}%s " % (max(1, round((nxt - w_start) * 100)), words[i]))
            out.append((start, end, "".join(parts).strip()))
        else:
            out.append((start, end, " ".join(words)))
    return out


def write_ass(lines, path, width=1080, height=1920, margin=None, size=None, config=None,
              line_groups=None):
    """Write captions as an .ass file ffmpeg can burn in. `lines` are
    (start, end, text); with `line_groups` (word timings) every look is possible."""
    config = dict(settings() if config is None else config)
    if size:
        config["size"] = min(SIZES, key=lambda k: abs(SIZES[k] - size))
    if line_groups is None:
        config["style"] = "classic" if config["style"] in ("pop", "karaoke") else config["style"]
        events = [(s, e, _escape(t, config["caps"])) for s, e, t in lines]
    else:
        events = _events(line_groups, config)
    body = "".join("Dialogue: 0,%s,%s,Cap,,0,0,0,,%s\n" % (_stamp(s), _stamp(e), text)
                   for s, e, text in events)
    path.write_text(_header(config, width, height) + body, encoding="utf-8")
    return path


def make_captions(source, ass_path, config=None):
    """Transcribe `source` into `ass_path`. Returns the number of caption lines
    (0 = nobody talks), or raises when captions cannot be made at all."""
    config = config or settings()
    words = transcribe(source, config)
    line_groups = groups(words)
    if line_groups:
        ass_path.parent.mkdir(parents=True, exist_ok=True)
        write_ass(chunks(words), ass_path, config=config, line_groups=line_groups)
    return len(line_groups)
