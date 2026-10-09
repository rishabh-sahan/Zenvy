"""Doctor page + API, and the patient's consent endpoints.

The doctor page (/doctor) talks only to the gateway, like the patient page.

* Recording upload and "regenerate note" do real work here (audio conversion,
  speech-to-text, the language model), so they are written out below.
* Everything else is a pass-through to Team C, limited to an explicit list of
  routes and carrying the doctor's own bearer token, so Team C decides what that
  doctor may see.
"""
import re
from pathlib import Path

import requests
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response

from services.scribe import audio, pipeline
from services.scribe.team_c import TEAM_C_BASE_URL, TeamC, TeamCError

router = APIRouter()

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
]
PATIENT_RULES = [
    ({"GET"}, rf"consent-message"),
    ({"GET"}, rf"patients/{ID}/appointments"),
    ({"POST"}, rf"appointments/{ID}/consent"),
    # the "Your appointments" card: pick a new time of the same doctor, move or cancel
    ({"GET"}, rf"doctors/{ID}/slots"),
    ({"POST"}, rf"appointments/{ID}/reschedule"),
    ({"POST"}, rf"appointments/{ID}/cancel"),
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
