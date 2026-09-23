"""Burned-in captions for Shorts: speech to text on this PC, no API, no cost.

Uses faster-whisper (an optional install: pip install faster-whisper). The
first use downloads the speech model once (~150 MB for "base.en"). With an
NVIDIA graphics card it runs on the GPU in a second or two per clip, otherwise
on the CPU, a little slower.

Captions are the short, punchy kind clip channels use: two or three words at
a time, big, white with a black outline, in the lower third - above the part
of the screen the Shorts / TikTok buttons cover.
"""

import sys
import threading

MODEL_NAME = "base.en"          # fast and good for English; "small.en" is more accurate
WORDS_PER_LINE = 3
MAX_CHARS = 18
MAX_SECONDS = 1.4
MAX_WORD_SECONDS = 0.9

GPU_ERRORS = ("cuda", "cudnn", "cublas", ".dll", ".so")   # GPU libraries missing
_model = None
_model_lock = threading.Lock()
_run_lock = threading.Lock()    # one transcription at a time: the model is shared

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


def available():
    """True when faster-whisper is installed."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def gpu_ready():
    """An NVIDIA card AND the CUDA libraries faster-whisper needs (cuBLAS, cuDNN).
    Without the libraries a GPU model loads fine and only fails - or hangs - when
    used, so they are looked for up front."""
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


def _load():
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            if gpu_ready():
                try:
                    _model = WhisperModel(MODEL_NAME, device="cuda", compute_type="float16")
                except Exception:
                    _model = None
            if _model is None:
                _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
        return _model


def transcribe(path, _retry=True):
    """[(start, end, word)] for the speech in a clip; [] when nothing is said."""
    gpu_failed = False
    with _run_lock:
        model = _load()     # inside the lock: after a switch to the CPU, everyone uses it
        try:
            segments, _info = model.transcribe(str(path), word_timestamps=True,
                                               vad_filter=True, beam_size=1)
            return [(w.start, w.end, w.word.strip()) for seg in segments
                    for w in (seg.words or []) if w.word.strip()]
        except RuntimeError as error:
            # A GPU without NVIDIA's CUDA libraries (cuBLAS, cuDNN) only fails here,
            # at the first real use. Switch to the CPU - outside the lock, which
            # the retry needs to take again.
            if not _retry or not any(word in str(error).lower() for word in GPU_ERRORS):
                raise
            gpu_failed = True
    if gpu_failed:
        _fallback_to_cpu()
        return transcribe(path, _retry=False)
    return []


def _fallback_to_cpu():
    global _model
    from faster_whisper import WhisperModel
    with _model_lock:
        _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")


def chunks(words):
    """Group words into short caption lines: [(start, end, text)]."""
    out, current = [], []
    # Whisper sometimes stretches a word over the silence after it; a caption
    # should not hang on screen for seconds after it was said.
    words = [(float(s), min(float(e), float(s) + MAX_WORD_SECONDS), w) for s, e, w in words]
    for start, end, word in words:
        text = " ".join(w for _s, _e, w in current + [(start, end, word)])
        if current and (len(current) >= WORDS_PER_LINE or len(text) > MAX_CHARS
                        or end - current[0][0] > MAX_SECONDS):
            out.append((current[0][0], current[-1][1], " ".join(w for _s, _e, w in current)))
            current = []
        current.append((start, end, word))
    if current:
        out.append((current[0][0], current[-1][1], " ".join(w for _s, _e, w in current)))
    # No gaps shorter than a blink between lines: they flicker.
    smoothed = []
    for i, (start, end, text) in enumerate(out):
        if i + 1 < len(out) and out[i + 1][0] - end < 0.25:
            end = out[i + 1][0]
        smoothed.append((start, end, text))
    return smoothed


def _stamp(seconds):
    seconds = max(0.0, seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return "%d:%02d:%05.2f" % (hours, minutes, secs)


def _escape(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")").upper()


def write_ass(lines, path, width=1080, height=1920, margin=600, size=84):
    """Write caption lines as an .ass subtitle file ffmpeg can burn in."""
    body = "".join("Dialogue: 0,%s,%s,Cap,,0,0,0,,%s\n" % (_stamp(s), _stamp(e), _escape(t))
                   for s, e, t in lines)
    path.write_text(ASS_HEADER.format(w=width, h=height, size=size, margin=margin) + body,
                    encoding="utf-8")
    return path


def make_captions(source, ass_path):
    """Transcribe `source` into `ass_path`. Returns the number of caption lines
    (0 = nobody talks), or raises when captions cannot be made at all."""
    lines = chunks(transcribe(source))
    if lines:
        ass_path.parent.mkdir(parents=True, exist_ok=True)
        write_ass(lines, ass_path)
    return len(lines)
