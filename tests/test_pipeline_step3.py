import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from services.orchestrator import pipeline
from services.orchestrator.pipeline import run_pipeline, detect_script_lang, PipelineError

KN = "ನಮಸ್ಕಾರ, ನಾನು ಸಹಾಯ ಮಾಡಬಹುದೇ?"
HI = "नमस्ते, मैं आपकी कैसे मदद कर सकता हूँ?"
EN = "Hello, how can I help you?"


def patch_stages(monkeypatch, lang, reply, translate=None):
    monkeypatch.setattr(pipeline, "run_stt", lambda p: {"text": "q", "language_code": f"{lang}-IN", "short_lang": lang})
    monkeypatch.setattr(pipeline, "run_nlu", lambda t: {"intent": "General_FAQ", "entities": {}})
    monkeypatch.setattr(pipeline, "run_llm", lambda t, l: reply)
    if translate is not None:
        monkeypatch.setattr(pipeline, "translate_text", translate)


def test_detect_script():
    assert detect_script_lang(KN) == "kn"
    assert detect_script_lang(HI) == "hi"
    assert detect_script_lang(EN) == "en"
    assert detect_script_lang("123 !!") is None


@pytest.mark.parametrize("lang,reply", [("kn", KN), ("hi", HI), ("en", EN)])
def test_same_language_skips_translate(monkeypatch, lang, reply):
    def must_not_call(*a, **k):
        raise AssertionError("translate should not be called")
    patch_stages(monkeypatch, lang, reply, must_not_call)
    out = run_pipeline("x.wav")
    assert out["translated"] is False
    assert out["response_text"] == reply
    assert out["target_language"] == lang


def test_cross_language_translates(monkeypatch):
    patch_stages(monkeypatch, "hi", HI, lambda text, s, t: KN)
    out = run_pipeline("x.wav", target_lang="kn")
    assert out["translated"] is True
    assert out["response_text"] == KN
    assert out["llm_text"] == HI


def test_wrong_language_reply_gets_fixed(monkeypatch):
    patch_stages(monkeypatch, "kn", EN, lambda text, s, t: KN)
    out = run_pipeline("x.wav")
    assert out["translated"] is True
    assert out["response_text"] == KN


def test_translate_failure_falls_back(monkeypatch):
    def boom(*a, **k):
        raise PipelineError("translate", "down")
    patch_stages(monkeypatch, "hi", HI, boom)
    out = run_pipeline("x.wav", target_lang="kn")
    assert out["translated"] is False
    assert out["response_text"] == HI
    assert out["warnings"]


def test_stage_failure_propagates(monkeypatch):
    def bad(p):
        raise PipelineError("stt", "not reachable")
    monkeypatch.setattr(pipeline, "run_stt", bad)
    with pytest.raises(PipelineError) as e:
        run_pipeline("x.wav")
    assert e.value.stage == "stt"


def test_bad_target_lang():
    with pytest.raises(PipelineError):
        run_pipeline("x.wav", target_lang="fr")


def test_output_has_all_fields(monkeypatch):
    patch_stages(monkeypatch, "en", EN)
    out = run_pipeline("x.wav")
    for key in ["transcript", "intent", "entities", "llm_text", "response_text", "timings_ms"]:
        assert key in out
    assert "total" in out["timings_ms"]