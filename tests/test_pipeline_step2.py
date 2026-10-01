import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import requests

from services.orchestrator import pipeline
from services.orchestrator.pipeline import run_stt, PipelineError


class FakeResponse:
    def __init__(self, ok=True, status_code=200, payload=None, text=""):
        self.ok = ok
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "test.wav"
    p.write_bytes(b"RIFFfakewavdata")
    return str(p)


def test_stt_success_maps_language(monkeypatch, wav):
    monkeypatch.setattr(
        pipeline.requests, "post",
        lambda *a, **k: FakeResponse(payload={"text": "ನಮಸ್ಕಾರ", "language_code": "kn-IN"}),
    )
    result = run_stt(wav)
    assert result["short_lang"] == "kn"
    assert result["text"] == "ನಮಸ್ಕಾರ"


def test_stt_unsupported_language(monkeypatch, wav):
    monkeypatch.setattr(
        pipeline.requests, "post",
        lambda *a, **k: FakeResponse(payload={"text": "hola", "language_code": "es-ES"}),
    )
    with pytest.raises(PipelineError) as e:
        run_stt(wav)
    assert e.value.stage == "stt"


def test_stt_empty_transcript(monkeypatch, wav):
    monkeypatch.setattr(
        pipeline.requests, "post",
        lambda *a, **k: FakeResponse(payload={"text": "  ", "language_code": "en-IN"}),
    )
    with pytest.raises(PipelineError):
        run_stt(wav)


def test_stt_service_down(monkeypatch, wav):
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError()
    monkeypatch.setattr(pipeline.requests, "post", boom)
    with pytest.raises(PipelineError) as e:
        run_stt(wav)
    assert "not reachable" in str(e.value)


def test_stt_missing_file():
    with pytest.raises(PipelineError):
        run_stt("does_not_exist.wav")