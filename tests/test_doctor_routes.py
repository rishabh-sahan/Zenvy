"""
The gateway's doctor / consent endpoints. Team C and the scribe are faked; only
the gateway's own behaviour is tested: which routes may pass through, how the
doctor's token is forwarded, and what happens to an uploaded recording.
"""
import io
import math
import shutil
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.gateway import doctor_routes
from services.scribe import pipeline
from services.scribe.team_c import TeamCError

app = FastAPI()
app.include_router(doctor_routes.router)
client = TestClient(app)

AUTH = {"Authorization": "Bearer doctor-token"}


def wav_bytes(seconds=2.0, rate=16000):
    n = int(rate * seconds)
    samples = [int(6000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(n)]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack(f"<{n}h", *samples))
    return buffer.getvalue()


class FakeUpstream:
    def __init__(self, status_code=200, content=b'{"ok": true}', content_type="application/json"):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type}


@pytest.fixture
def upstream(monkeypatch):
    """Capture what the gateway sends to Team C, and answer with a canned reply."""
    seen = {}
    reply = {"value": FakeUpstream()}

    def fake_request(method, url, params=None, data=None, headers=None, timeout=None):
        seen.update(method=method, url=url, params=params, data=data, headers=headers)
        if isinstance(reply["value"], Exception):
            raise reply["value"]
        return reply["value"]

    monkeypatch.setattr(requests, "request", fake_request)
    seen["reply"] = reply
    return seen


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------

def test_the_doctor_page_is_served():
    response = client.get("/doctor")
    assert response.status_code == 200
    assert "Doctor sign in" in response.text


# ---------------------------------------------------------------------------
# pass-through to Team C
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("POST", "auth/staff/login"),
    ("GET", "doctor/appointments"),
    ("GET", "appointments/abc-123/consent"),
    ("POST", "appointments/abc-123/consent"),
    ("GET", "consent-message"),
    ("POST", "consultations"),
    ("GET", "consultations/c1"),
    ("PATCH", "consultations/c1/turns/t1"),
    ("POST", "consultations/c1/notes"),
    ("POST", "consultations/c1/notes/n1/approve"),
    ("DELETE", "consultations/c1/recording"),
])
def test_the_listed_routes_pass_through(upstream, method, path):
    response = client.request(method, f"/doctor/api/{path}", headers=AUTH, json={"x": 1} if method != "GET" and method != "DELETE" else None)
    assert response.status_code == 200
    assert upstream["method"] == method
    assert upstream["url"].endswith(f"/api/v1/{path}")


@pytest.mark.parametrize("method,path", [
    ("POST", "auth/login"),                 # the password login of patients
    ("POST", "auth/register"),
    ("GET", "audit/session/s1"),
    ("POST", "appointments"),               # booking is not the doctor page's job
    ("POST", "appointments/a1/cancel"),
    ("GET", "consultations"),               # no listing of every consultation
    ("PUT", "consultations/c1/transcript"), # only the scribe writes transcripts
    ("PATCH", "consultations/c1/status"),
    ("DELETE", "consultations/c1"),
    ("GET", "patients/p1/appointments"),    # patient route, not a doctor route
    ("GET", "doctors"),
    ("GET", "../../healthz"),
])
def test_everything_else_is_blocked(upstream, method, path):
    response = client.request(method, f"/doctor/api/{path}", headers=AUTH)
    assert response.status_code == 404
    assert "url" not in upstream  # nothing reached Team C


def test_the_doctors_token_query_and_body_are_forwarded_and_the_answer_returned(upstream):
    upstream["reply"]["value"] = FakeUpstream(409, b'{"detail": "consent_required"}')
    response = client.post(
        "/doctor/api/consultations?x=1", headers=AUTH, json={"appointment_id": "a1"}
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "consent_required"}
    assert upstream["headers"]["Authorization"] == "Bearer doctor-token"
    assert upstream["params"] == {"x": "1"}
    assert b"appointment_id" in upstream["data"]


def test_without_a_token_nothing_is_invented(upstream):
    client.get("/doctor/api/doctor/appointments")
    assert "Authorization" not in upstream["headers"]  # Team C will answer 401


def test_team_c_being_down_is_a_clear_502(upstream):
    upstream["reply"]["value"] = requests.exceptions.ConnectionError("down")
    response = client.get("/doctor/api/doctor/appointments", headers=AUTH)
    assert response.status_code == 502
    assert "not reachable" in response.json()["detail"]


# ---------------------------------------------------------------------------
# patient consent endpoints
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("GET", "consent-message"),
    ("GET", "patients/p-1/appointments"),
    ("POST", "appointments/a-1/consent"),
])
def test_the_patient_can_reach_only_the_consent_routes(upstream, method, path):
    response = client.request(method, f"/channels/web/consultation/{path}", json={"consent_given": True} if method == "POST" else None)
    assert response.status_code == 200
    assert upstream["url"].endswith(f"/api/v1/{path}")


@pytest.mark.parametrize("method,path", [
    ("GET", "consultations/c1"),
    ("GET", "doctor/appointments"),
    ("POST", "consultations"),
    ("GET", "appointments/a1/consent"),  # reading consent needs auth_id: not offered here
])
def test_the_patient_cannot_reach_clinical_routes(upstream, method, path):
    assert client.request(method, f"/channels/web/consultation/{path}").status_code == 404
    assert "url" not in upstream


# ---------------------------------------------------------------------------
# recording upload
# ---------------------------------------------------------------------------

class FakeTeamC:
    instances = []

    def __init__(self, token):
        self.token = token
        self.uploaded = None
        self.error = FakeTeamC.error
        FakeTeamC.instances.append(self)

    error = None

    def get_consultation(self, cid):
        if self.error and self.error[0] == "get":
            raise TeamCError(*self.error[1:])
        return {"consultation_id": cid}

    def upload_audio(self, cid, wav):
        if self.error and self.error[0] == "upload":
            raise TeamCError(*self.error[1:])
        self.uploaded = wav
        return {"consultation_id": cid, "status": "audio_uploaded"}


@pytest.fixture
def scribe(monkeypatch):
    FakeTeamC.instances = []
    FakeTeamC.error = None
    runs = []
    monkeypatch.setattr(doctor_routes, "TeamC", FakeTeamC)
    monkeypatch.setattr(pipeline, "process_recording", lambda cid, wav, token: runs.append((cid, len(wav), token)))
    return runs


def upload(content, name="recording.wav", headers=AUTH):
    return client.post(
        "/doctor/api/consultations/c1/audio",
        files={"file": (name, content, "audio/wav")},
        headers=headers,
    )


def test_a_recording_is_stored_and_processing_starts(scribe):
    response = upload(wav_bytes(2.0))
    assert response.status_code == 200
    assert response.json()["status"] == "audio_uploaded"
    team_c = FakeTeamC.instances[-1]
    assert team_c.token == "doctor-token"
    assert team_c.uploaded[:4] == b"RIFF"
    # Processing ran in the background with the same audio, for the same doctor.
    assert scribe == [("c1", len(team_c.uploaded), "doctor-token")]


def test_uploading_needs_a_login(scribe):
    assert upload(wav_bytes(1.0), headers={}).status_code == 401
    assert scribe == []


def test_a_consultation_that_is_not_yours_is_refused_before_any_work(scribe):
    FakeTeamC.error = ("get", 403, "This is not your consultation")
    response = upload(wav_bytes(1.0))
    assert response.status_code == 403
    assert response.json()["detail"] == "This is not your consultation"
    assert FakeTeamC.instances[-1].uploaded is None and scribe == []


def test_team_c_refusing_the_recording_is_passed_on_and_nothing_is_processed(scribe):
    FakeTeamC.error = ("upload", 409, "consent_required")
    response = upload(wav_bytes(1.0))
    assert response.status_code == 409 and response.json()["detail"] == "consent_required"
    assert scribe == []


def test_an_empty_file_is_refused(scribe):
    assert upload(b"").status_code == 422
    assert scribe == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed on this machine")
def test_a_file_that_is_not_audio_is_refused_clearly(scribe):
    response = upload(b"this is definitely not audio", name="notes.webm")
    assert response.status_code == 422
    assert "audio" in response.json()["detail"].lower()
    assert scribe == []


def test_an_oversized_upload_is_refused(scribe, monkeypatch):
    monkeypatch.setattr(doctor_routes, "MAX_UPLOAD_BYTES", 100)
    assert upload(wav_bytes(1.0)).status_code == 413
    assert scribe == []


# ---------------------------------------------------------------------------
# regenerate
# ---------------------------------------------------------------------------

def test_regenerate_returns_the_new_version(monkeypatch):
    monkeypatch.setattr(pipeline, "regenerate_note", lambda cid, token: {"version": 3, "source": "ai_regenerated"})
    response = client.post("/doctor/api/consultations/c1/regenerate", headers=AUTH)
    assert response.status_code == 200 and response.json()["version"] == 3


def test_regenerate_without_a_transcript_is_a_conflict(monkeypatch):
    def fail(cid, token):
        raise pipeline.PipelineFailure("no_transcript")

    monkeypatch.setattr(pipeline, "regenerate_note", fail)
    response = client.post("/doctor/api/consultations/c1/regenerate", headers=AUTH)
    assert response.status_code == 409 and response.json()["detail"] == "no_transcript"


def test_regenerate_model_failure_is_a_502_with_the_reason(monkeypatch):
    def fail(cid, token):
        raise pipeline.PipelineFailure("summary_failed")

    monkeypatch.setattr(pipeline, "regenerate_note", fail)
    response = client.post("/doctor/api/consultations/c1/regenerate", headers=AUTH)
    assert response.status_code == 502 and response.json()["detail"] == "summary_failed"


def test_regenerate_passes_team_c_errors_on(monkeypatch):
    def fail(cid, token):
        raise TeamCError(403, "This is not your consultation")

    monkeypatch.setattr(pipeline, "regenerate_note", fail)
    assert client.post("/doctor/api/consultations/c1/regenerate", headers=AUTH).status_code == 403


def test_regenerate_needs_a_login():
    assert client.post("/doctor/api/consultations/c1/regenerate").status_code == 401
