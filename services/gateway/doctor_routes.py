"""Doctor page + API, and the patient's consent endpoints.

The doctor page (/doctor) talks only to the gateway, like the patient page.

* Recording upload and "regenerate note" do real work here (audio conversion,
  speech-to-text, the language model), so they are written out below.
* Everything else is a pass-through to Team C, limited to an explicit list of
  routes and carrying the doctor's own bearer token, so Team C decides what that
  doctor may see.
"""
import os
import re
import threading
from pathlib import Path

import requests
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response

from pydantic import BaseModel

from services.agents import doctor_agent
from services.scribe import audio, live, medication, pipeline, transcribe
from services.scribe.team_c import TEAM_C_BASE_URL, TeamC, TeamCError

router = APIRouter()

LIVE_ASSIST_ENABLED = os.getenv("LIVE_ASSIST_ENABLED", "true").strip().lower() == "true"

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

ID = r"[\w-]+"
# (methods, path pattern) - the only Team C routes the doctor page may reach.
PROXY_RULES = [
    ({"POST"}, rf"auth/staff/login"),
    ({"GET"}, rf"doctor/appointments"),
    ({"GET", "POST"}, rf"appointments/{ID}/consent"),
    ({"GET"}, rf"consent-message"),
    ({"POST"}, rf"consultations"),
    ({"GET"}, rf"consultations/{ID}"),
    ({"PATCH"}, rf"consultations/{ID}/turns/{ID}"),
    ({"POST"}, rf"consultations/{ID}/notes"),
    ({"POST"}, rf"consultations/{ID}/notes/{ID}/approve"),
    ({"DELETE"}, rf"consultations/{ID}/recording"),
    ({"GET", "PUT", "DELETE"}, rf"consultations/{ID}/follow-up"),
    ({"POST"}, rf"consultations/{ID}/follow-up/book"),
    ({"GET"}, rf"appointments/{ID}/history"),
    # prescriptions and the doctor agent's inbox
    ({"GET", "PUT"}, rf"consultations/{ID}/prescription"),
    ({"POST"}, rf"consultations/{ID}/prescription/sign"),
    ({"POST"}, rf"consultations/{ID}/prescription/carry-forward"),
    ({"GET"}, rf"agent/messages"),
    ({"POST"}, rf"agent/messages/read"),
    ({"GET"}, rf"appointments/{ID}/patient-history"),
    # Live Assist: the doctor dismisses, pins or adds a suggestion to the plan
    ({"PATCH"}, rf"consultations/{ID}/live/suggestions/{ID}"),
]
PATIENT_RULES = [
    ({"GET"}, rf"consent-message"),
    ({"GET"}, rf"patients/{ID}/appointments"),
    ({"POST"}, rf"appointments/{ID}/consent"),
    # the "Your appointments" card: pick a new time of the same doctor, move or cancel
    ({"GET"}, rf"doctors/{ID}/slots"),
    ({"POST"}, rf"appointments/{ID}/reschedule"),
    ({"POST"}, rf"appointments/{ID}/cancel"),
    # "Your medicines": the signed medicines, mark a dose taken, clear the coordinator's notes
    ({"GET"}, rf"patients/{ID}/medications"),
    ({"POST"}, rf"patients/{ID}/doses/taken"),
    ({"POST"}, rf"patients/{ID}/doses/{ID}/taken"),
    ({"POST"}, rf"patients/{ID}/messages/read"),
]


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Please log in again.")
    return token.strip()


def _allowed(rules, method: str, path: str) -> bool:
    return any(method in methods and re.fullmatch(pattern, path) for methods, pattern in rules)


async def _forward(request: Request, path: str, rules) -> Response:
    method = request.method.upper()
    if not _allowed(rules, method, path):
        raise HTTPException(status_code=404, detail="Not found")

    body = await request.body()
    headers = {}
    if request.headers.get("authorization"):
        headers["Authorization"] = request.headers["authorization"]
    if body:
        headers["Content-Type"] = request.headers.get("content-type", "application/json")

    try:
        upstream = await run_in_threadpool(
            lambda: requests.request(
                method,
                f"{TEAM_C_BASE_URL.rstrip('/')}/api/v1/{path}",
                params=dict(request.query_params),
                data=body or None,
                headers=headers,
                timeout=60,
            )
        )
    except requests.exceptions.RequestException:
        return JSONResponse(status_code=502, content={"detail": "The hospital data service is not reachable."})

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


# ---------------------------------------------------------------------------
# doctor page
# ---------------------------------------------------------------------------

@router.get("/doctor")
async def doctor_page():
    return FileResponse(STATIC_DIR / "doctor.html")


@router.post("/doctor/api/consultations/{consultation_id}/audio")
async def upload_recording(
    consultation_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """Take a recording (any browser format), store it encrypted, and start the scribe."""
    token = _bearer(request)
    team_c = TeamC(token)

    # Ask Team C first (cheap): is this the doctor's own consultation, with consent?
    try:
        await run_in_threadpool(team_c.get_consultation, consultation_id)
    except TeamCError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="The recording is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The recording is too large.")

    suffix = Path(file.filename or "").suffix or (".wav" if "wav" in (file.content_type or "") else ".webm")
    try:
        wav = await run_in_threadpool(audio.to_wav_16k_mono, raw, suffix)
        audio.wav_seconds(wav)  # also validates the converted file
    except audio.AudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        stored = await run_in_threadpool(team_c.upload_audio, consultation_id, wav)
    except TeamCError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    background_tasks.add_task(pipeline.process_recording, consultation_id, wav, token)
    return stored


@router.post("/doctor/api/consultations/{consultation_id}/regenerate")
async def regenerate(consultation_id: str, request: Request):
    """A fresh AI draft from the current transcript, saved as a new version."""
    token = _bearer(request)
    try:
        return await run_in_threadpool(pipeline.regenerate_note, consultation_id, token)
    except TeamCError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    except pipeline.PipelineFailure as failure:
        status = 409 if failure.code == "no_transcript" else 502
        return JSONResponse(status_code=status, content={"detail": failure.code})


# ---------------------------------------------------------------------------
# Live Assist: suggestions for the doctor while the consultation is going on
# ---------------------------------------------------------------------------

def _history_lines(visits: list[dict]) -> list[str]:
    lines = []
    for visit in visits[:5]:
        parts = [f"complaint: {visit.get('chief_complaint') or '-'}"]
        if visit.get("assessment"):
            parts.append(f"assessment: {visit['assessment']}")
        if visit.get("plan"):
            parts.append(f"plan: {visit['plan']}")
        if visit.get("medicines"):
            parts.append("medicines: " + ", ".join(visit["medicines"]))
        lines.append(f"{visit.get('date', '?')}: " + "; ".join(parts))
    return lines


def _live_disabled():
    if not LIVE_ASSIST_ENABLED:
        raise HTTPException(status_code=404, detail="Live Assist is switched off.")


class PauseIn(BaseModel):
    paused: bool


@router.get("/doctor/api/live/config")
async def live_config():
    return {"enabled": LIVE_ASSIST_ENABLED, "chunk_seconds": live.CHUNK_SECONDS, "visible_cards": live.MAX_CARDS_VISIBLE}


@router.post("/doctor/api/consultations/{consultation_id}/live/audio")
async def live_audio(consultation_id: str, request: Request, file: UploadFile = File(...)):
    """One short piece of the conversation (about 6 seconds of 16 kHz WAV). Never affects the recording."""
    _live_disabled()
    token = _bearer(request)
    team_c = TeamC(token)

    try:
        detail = await run_in_threadpool(team_c.get_consultation, consultation_id)   # owner? (403/404 otherwise)
    except TeamCError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    if detail.get("consent_state") != "granted":
        live.close_session(consultation_id)
        return JSONResponse(status_code=409, content={"detail": "consent_required"})

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="The audio piece is empty.")
    if len(raw) > live.MAX_CHUNK_BYTES:
        raise HTTPException(status_code=413, detail="The audio piece is too large.")

    session = live.get_session(consultation_id)
    if session is None:
        history: list[str] = []
        try:
            history = _history_lines(await run_in_threadpool(team_c.patient_history, detail["appointment_id"]))
        except TeamCError:
            pass                                       # no history is fine: Live Assist still works
        session = live.open_session(consultation_id, detail.get("appointment_id"), history, token)
    session.token = token

    if session.paused:
        return {"ok": True, "state": "paused", "added_words": 0}

    try:
        wav = raw if raw[:4] == b"RIFF" else await run_in_threadpool(audio.to_wav_16k_mono, raw, ".webm")
        added = await run_in_threadpool(live.ingest, session, wav)
    except audio.AudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except transcribe.SttError:
        session.error = "speech_unavailable"
        return {"ok": False, "state": "error", "error": "speech_unavailable", "added_words": 0}

    def store(cards: list[dict]) -> list[dict]:
        try:
            return TeamC(session.token).add_live_suggestions(consultation_id, cards)
        except TeamCError as exc:
            if exc.status_code in (403, 404, 409):       # consent withdrawn / not ours any more: stop
                live.close_session(consultation_id)
            raise

    # Red-flag phrases are shown at once: they never wait for the model or for the interval.
    flags = live.immediate_red_flags(session)
    if flags:
        try:
            await run_in_threadpool(store, flags)
        except TeamCError:
            pass

    if live.claim_engine_run(session):
        threading.Thread(target=live.run_engine, args=(session, store), daemon=True).start()
    else:
        live.schedule_deferred_run(session, store)       # (so speech that arrives early is not forgotten)

    return {"ok": True, "state": live.snapshot(session)["state"], "added_words": added}


@router.get("/doctor/api/consultations/{consultation_id}/live")
async def live_state(consultation_id: str, request: Request):
    """Everything the Live Assist panel shows: state, the latest lines, and the suggestion cards."""
    _live_disabled()
    token = _bearer(request)
    try:
        cards = await run_in_threadpool(TeamC(token).list_live_suggestions, consultation_id)
    except TeamCError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    session = live.get_session(consultation_id)
    state = live.snapshot(session) if session else {
        "state": "idle", "error": None, "chunks": 0, "seconds": 0.0, "turns": [], "turn_count": 0}
    return {"enabled": True, "visible_cards": live.MAX_CARDS_VISIBLE, **state, "suggestions": cards}


@router.post("/doctor/api/consultations/{consultation_id}/live/pause")
async def live_pause(consultation_id: str, payload: PauseIn, request: Request):
    _live_disabled()
    token = _bearer(request)
    try:
        await run_in_threadpool(TeamC(token).list_live_suggestions, consultation_id)    # ownership check
    except TeamCError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    session = live.get_session(consultation_id)
    if session is not None:
        session.paused = payload.paused
    return {"paused": payload.paused, "state": live.snapshot(session)["state"] if session else "idle"}


@router.post("/doctor/api/consultations/{consultation_id}/live/end")
async def live_end(consultation_id: str, request: Request):
    """Recording stopped: forget the in-memory conversation. The cards stay for the doctor to review."""
    _live_disabled()
    token = _bearer(request)
    try:
        await run_in_threadpool(TeamC(token).list_live_suggestions, consultation_id)
    except TeamCError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return {"ended": live.close_session(consultation_id)}


@router.post("/doctor/api/consultations/{consultation_id}/prescription/draft")
async def draft_prescription(consultation_id: str, request: Request):
    """A DRAFT medicine list read from the transcript, for the doctor to check and sign."""
    token = _bearer(request)
    team_c = TeamC(token)

    def work():
        detail = team_c.get_consultation(consultation_id)
        notes = detail.get("notes") or []
        items, dropped = medication.extract_medications(detail.get("turns") or [], notes[-1].get("plan", "") if notes else "")
        if not items:
            return None, dropped
        return team_c.put_prescription(consultation_id, items, "transcript"), dropped

    try:
        saved, dropped = await run_in_threadpool(work)
    except TeamCError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    except medication.MedicationError as exc:
        status = 409 if exc.code == "no_transcript" else 502
        return JSONResponse(status_code=status, content={"detail": exc.code})
    if saved is None:
        return JSONResponse(status_code=422, content={"detail": "no_medicines_found", "dropped": dropped})
    return {**saved, "dropped": dropped}


class AssistantIn(BaseModel):
    message: str
    appointment_id: str | None = None
    consultation_id: str | None = None


@router.post("/doctor/api/assistant")
async def assistant(payload: AssistantIn, request: Request):
    """The doctor agent: look-ups and drafts for this doctor. It never signs or approves anything."""
    token = _bearer(request)
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Please type a question.")
    if len(message) > 500:
        raise HTTPException(status_code=422, detail="Please keep it under 500 characters.")
    try:
        return await run_in_threadpool(
            doctor_agent.handle, message, token, payload.appointment_id, payload.consultation_id
        )
    except TeamCError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@router.api_route("/doctor/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def doctor_api(path: str, request: Request):
    return await _forward(request, path, PROXY_RULES)


# ---------------------------------------------------------------------------
# patient: consent for recording
# ---------------------------------------------------------------------------

@router.api_route("/channels/web/consultation/{path:path}", methods=["GET", "POST"])
async def patient_consultation_api(path: str, request: Request):
    """consent-message, patients/{auth_id}/appointments, appointments/{id}/consent."""
    return await _forward(request, path, PATIENT_RULES)
