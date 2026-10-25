"""Push-to-talk voice input and spoken answers for the Operations Assistant.

Speech recognition (STT) and synthesis (TTS) run entirely in the operator's
browser through the Web Speech API; no audio reaches the backend and no paid
service is involved. The text path remains primary: this component only fills
in the question and reads answers aloud.

Browser support: SpeechRecognition is available in Chrome and Edge (desktop and
Android). Firefox has no SpeechRecognition, so the button is disabled with a
hint there; speechSynthesis works in all modern browsers. Chrome's recogniser
sends audio to the browser vendor's speech service, which needs network access.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit.components.v1 as components

_COMPONENT_DIR = Path(__file__).resolve().parent / "voice_component"
_voice = components.declare_component("zenith_voice", path=str(_COMPONENT_DIR))


def voice_input(
    *,
    speak_text: str = "",
    speak_id: Any = None,
    auto_speak: bool = False,
    lang: str = "en-US",
    compact: bool = False,
    key: str = "zenith_voice",
) -> dict[str, Any] | None:
    """Render the push-to-talk widget (``compact=True``: a single round mic pill).

    Returns ``{"transcript": str, "ts": int}`` after the operator finishes
    speaking, otherwise ``None``. ``ts`` changes on every utterance so callers
    can tell a new transcript from a rerun with the same value.
    """
    return _voice(speak_text=speak_text, speak_id=speak_id, auto_speak=auto_speak, lang=lang, compact=compact,
                  key=key, default=None)
