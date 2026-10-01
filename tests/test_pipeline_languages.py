import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from services.orchestrator import pipeline
from services.orchestrator.pipeline import run_pipeline, detect_script_lang

REPLIES = {
    "kn": "ನಮಸ್ಕಾರ, ನಾನು ಸಹಾಯ ಮಾಡಬಹುದೇ?",
    "hi": "नमस्ते, मैं आपकी कैसे मदद कर सकता हूँ?",
    "en": "Hello, how can I help you?",
}
LANGS = ["kn", "hi", "en"]


@pytest.mark.parametrize("src", LANGS)
@pytest.mark.parametrize("tgt", LANGS)
def test_language_matrix(monkeypatch, src, tgt):
    monkeypatch.setattr(pipeline, "run_stt", lambda p: {"text": "q", "language_code": f"{src}-IN", "short_lang": src})
    monkeypatch.setattr(pipeline, "run_nlu", lambda t: {"intent": "General_FAQ", "entities": {}})
    monkeypatch.setattr(pipeline, "run_llm", lambda t, l: REPLIES[src])
    monkeypatch.setattr(pipeline, "translate_text", lambda text, s, t: REPLIES[t])

    out = run_pipeline("x.wav", target_lang=tgt)

    assert detect_script_lang(out["response_text"]) == tgt
    assert out["translated"] == (src != tgt)
    assert out["input_language"] == src
    assert out["target_language"] == tgt
    assert out["warnings"] == []