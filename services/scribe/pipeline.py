"""Recording -> transcript -> English note, reporting progress to Team C.

Failure reasons stored on the consultation (shown to the doctor):

    no_speech              nothing but silence / noise was recorded
    speech_to_text_failed  the speech-to-text service kept failing
    summary_failed         the language model could not write a note
    summary_not_english    the note could not be produced in English
    audio_unreadable       the recording could not be read
    unexpected_error       anything else (details are in the gateway log)
"""
import threading

from services.scribe import audio, summarize, transcribe
from services.scribe.team_c import TeamC, TeamCError

# One run per consultation at a time (a double click must not start two).
_running: set[str] = set()
_lock = threading.Lock()


class PipelineFailure(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _safe_status(team_c: TeamC, consultation_id: str, status: str, reason: str | None = None) -> None:
    """Progress updates must never break the run."""
    try:
        team_c.set_status(consultation_id, status, reason)
    except TeamCError as exc:
        print(f"[Scribe] Could not set status {status!r}: {exc}")


def process_recording(
    consultation_id: str,
    wav_bytes: bytes,
    token: str,
    team_c: TeamC | None = None,
    stt_url: str | None = None,
    post=None,
    chat=None,
    translate=None,
) -> str:
    """Transcribe a stored recording and write the first draft note.

    Returns "draft_ready", or "failed:<reason>", or "already_running". Never raises.
    """
    team_c = team_c or TeamC(token)
    with _lock:
        if consultation_id in _running:
            return "already_running"
        _running.add(consultation_id)

    try:
        _safe_status(team_c, consultation_id, "transcribing")

        try:
            segments = audio.split_on_speech(wav_bytes)
        except audio.AudioError as exc:
            raise PipelineFailure("audio_unreadable", str(exc)) from exc
        if not segments:
            raise PipelineFailure("no_speech", "No speech was found in the recording.")
        print(f"[Scribe] {consultation_id}: {len(segments)} speech segment(s)")

        kwargs = {}
        if post is not None:
            kwargs["post"] = post
        try:
            results = transcribe.transcribe_segments(segments, stt_url, **kwargs)
        except transcribe.SttError as exc:
            raise PipelineFailure("speech_to_text_failed", str(exc)) from exc

        turns = transcribe.label_turns(segments, results)
        if not turns:
            raise PipelineFailure("no_speech", "The recording had no recognisable speech.")
        team_c.put_transcript(consultation_id, turns, transcribe.dominant_language(turns))

        _safe_status(team_c, consultation_id, "summarising")
        try:
            note = summarize.summarize(
                turns,
                **({"chat": chat} if chat else {}),
                **({"translate": translate} if translate else {}),
            )
        except summarize.SummaryError as exc:
            raise PipelineFailure(exc.code, str(exc)) from exc
        team_c.add_note(consultation_id, note, "ai")
        return "draft_ready"

    except PipelineFailure as failure:
        print(f"[Scribe] {consultation_id} failed: {failure.code}: {failure}")
        _safe_status(team_c, consultation_id, "failed", failure.code)
        return f"failed:{failure.code}"
    except Exception as exc:  # noqa: BLE001 - the background task must always finish cleanly
        print(f"[Scribe] {consultation_id} unexpected error: {exc!r}")
        _safe_status(team_c, consultation_id, "failed", "unexpected_error")
        return "failed:unexpected_error"
    finally:
        with _lock:
            _running.discard(consultation_id)


def regenerate_note(
    consultation_id: str, token: str, team_c: TeamC | None = None, chat=None, translate=None
) -> dict:
    """A fresh AI draft from the current (doctor-corrected) transcript.

    Saved as a new version; earlier versions, including an approved one, stay.
    Raises PipelineFailure; returns the saved note.
    """
    team_c = team_c or TeamC(token)
    detail = team_c.get_consultation(consultation_id)
    turns = detail.get("turns") or []
    if not turns:
        raise PipelineFailure("no_transcript", "There is no transcript to write a note from.")

    _safe_status(team_c, consultation_id, "summarising")
    try:
        note = summarize.summarize(
            turns,
            **({"chat": chat} if chat else {}),
            **({"translate": translate} if translate else {}),
        )
    except summarize.SummaryError as exc:
        _safe_status(team_c, consultation_id, "failed", exc.code)
        raise PipelineFailure(exc.code, str(exc)) from exc
    return team_c.add_note(consultation_id, note, "ai_regenerated")
