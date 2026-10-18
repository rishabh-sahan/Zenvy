"""Basic tests for the STT service's /transcribe endpoint."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from services.stt.main import app

client = TestClient(app)


def test_rejects_wrong_file_type():
    """Uploading a non-audio file should return 400, not crash."""
    response = client.post(
        "/transcribe",
        files={"file": ("test.txt", b"not audio", "text/plain")},
    )
    assert response.status_code == 400


def test_rejects_empty_file():
    """An empty audio file should be rejected with a clean 400."""
    response = client.post(
        "/transcribe",
        files={"file": ("empty.wav", b"", "audio/wav")},
    )
    assert response.status_code == 400


def test_accepts_valid_audio():
    """A real WAV file should return 200 with a transcript."""
    with open("scripts/output_en-IN.wav", "rb") as f:
        response = client.post(
            "/transcribe",
            files={"file": ("output_en-IN.wav", f, "audio/wav")},
        )
    assert response.status_code == 200
    assert "text" in response.json()

class _SarvamReply:
    ok = True
    status_code = 200
    text = ""

    def json(self):
        return {"transcript": "Hello, please sit down.", "language_code": "kn-IN"}


def _post_wav(monkeypatch, **data):
    sent = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        sent.update(data=data)
        return _SarvamReply()

    monkeypatch.setattr("services.stt.main.requests.post", fake_post)
    response = client.post("/transcribe", files={"file": ("a.wav", b"RIFFxxxxWAVE", "audio/wav")}, data=data)
    return response, sent


def test_the_default_mode_is_transcribe(monkeypatch):
    response, sent = _post_wav(monkeypatch)
    assert response.status_code == 200 and sent["data"]["mode"] == "transcribe"


def test_translate_mode_is_passed_on_and_the_detected_language_is_returned(monkeypatch):
    response, sent = _post_wav(monkeypatch, mode="translate")
    assert sent["data"]["mode"] == "translate" and sent["data"]["language_code"] == "unknown"
    assert response.json() == {"text": "Hello, please sit down.", "language_code": "kn-IN"}


def test_an_unknown_mode_is_refused_before_anything_is_sent(monkeypatch):
    response, sent = _post_wav(monkeypatch, mode="codemix")
    assert response.status_code == 400 and sent == {}
