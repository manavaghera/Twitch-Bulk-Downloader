"""Studio > Captions: how accurate, which language, and how they look - used by
every Short the app makes (downloads, radar, streamers, Autopilot, auto clips)."""

from pathlib import Path

import streamlit as st

from . import captions, media, studio, ui_login, ui_theme

ACCURACY = {"auto": "Auto - the best this PC runs well", "fast": "Fast (base, ~150 MB)",
            "better": "Better (small, ~480 MB)",
            "best": "Best (large-v3-turbo, ~1.6 GB, for NVIDIA cards)"}
LANGUAGES = {"en": "English", "auto": "Any language (detected)"}


def render():
    if not captions.available():
        ui_theme.empty_state("💬", "Captions are not installed",
                             "Run `.venv\\Scripts\\pip install faster-whisper`, then restart.")
        return
    config = captions.settings()
    with st.container(border=True, key="card_captions"):
        ui_theme.step("💬", "Captions", "Speech to text on this PC - no API, no cost. Used by "
                                        "every Short the app makes when captions are on.")
        if ui_login.needs_login():
            st.caption("🔒 Sign in to change the captions.")
            return
        cols = st.columns(2)
        model = cols[0].selectbox("Accuracy", list(ACCURACY), format_func=ACCURACY.get,
                                  index=list(ACCURACY).index(config["model"]), key="cap_model")
        language = cols[1].selectbox("Language", list(LANGUAGES), format_func=LANGUAGES.get,
                                     index=list(LANGUAGES).index(config["language"]),
                                     key="cap_lang")
        chosen = captions.model_name(dict(config, model=model, language=language))
        st.caption("Runs on the **%s** with the *%s* model%s." % (
            captions.device(), chosen, "" if captions.gpu_ready() else
            " - an NVIDIA card with cuBLAS/cuDNN installed runs the large one in seconds"))
        style = st.segmented_control("Look", list(captions.STYLES),
                                     format_func=lambda k: captions.STYLES[k].split(" - ")[0],
                                     default=config["style"], required=True, key="cap_style")
        st.caption(captions.STYLES[style])
        cols = st.columns(4, vertical_alignment="bottom")
        position = cols[0].selectbox("Where", list(captions.POSITIONS),
                                     format_func=captions.POSITIONS.get,
                                     index=list(captions.POSITIONS).index(config["position"]),
                                     key="cap_pos")
        size = cols[1].segmented_control("Size", list(captions.SIZES), required=True,
                                         default=config["size"], key="cap_size",
                                         format_func=str.upper)
        color = cols[2].selectbox("Highlight", list(captions.COLORS), format_func=str.title,
                                  index=list(captions.COLORS).index(config["color"]),
                                  key="cap_color", disabled=style in ("classic", "boxed"))
        caps = cols[3].toggle("ALL CAPS", value=config["caps"], key="cap_caps")
        values = {"model": model, "language": language, "style": style, "position": position,
                  "size": size, "color": color, "caps": caps}
        if any(config.get(k) != v for k, v in values.items()):
            captions.save_settings(**values)
    preview()


def preview():
    rows = [r for r in studio.library() if r["has_file"]]
    if not rows:
        return
    with st.container(border=True, key="card_captions_preview"):
        ui_theme.step("👀", "Preview", "Ten seconds of your newest clip with these captions. "
                                      "The first run downloads the speech model once.")
        if st.button("Make a preview", icon=":material/visibility:", key="cap_preview"):
            with st.spinner("Listening and writing the captions…"):
                length = media.probe(rows[0]["file"])["duration"] or 10.0
                path, problem = studio.trim_short(
                    rows[0], 0.0, min(10.0, length), "blur", True, False,
                    target=studio.CACHE / "captions_preview.mp4")
            if problem:
                st.error(problem)
            else:
                st.session_state["cap_preview_path"] = str(path)
        shown = st.session_state.get("cap_preview_path")
        if shown and Path(shown).exists():
            st.video(shown)
