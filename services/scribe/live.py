"""Live Assist sessions: the conversation as it happens.

The doctor's browser sends short audio pieces while it records. Each piece is transcribed and appended to a
rolling transcript kept in this process's memory (it is NOT stored: the full recording is stored, encrypted,
by the normal upload at the end). From time to time the suggestion engine reads the transcript and writes a
few cards for the doctor.

Nothing here can affect the recording or the note: if speech-to-text or the model fails, the session just
reports it and carries on with the next piece.
"""
import os
import threading
import time
from dataclasses import dataclass, field

from services.agents import live_assist
from services.scribe import audio, summarize, transcribe

CHUNK_SECONDS = 6                    # how much audio the browser sends at once
ENGINE_MIN_INTERVAL_SECONDS = 12     # at most one suggestion run per this many seconds
MIN_NEW_WORDS = 8                    # ... and only when this much new speech has arrived
MAX_CARDS_VISIBLE = 5                # the page shows at most this many active cards
MAX_CHUNK_BYTES = 2 * 1024 * 1024
MAX_CHUNK_SECONDS = 20
MAX_SESSIONS = 25
SESSION_TTL_SECONDS = 3 * 60 * 60
TRANSCRIPT_TAIL_TURNS = 14           # what GET /live returns

# Live pieces are sent to speech-to-text in "translate" mode: Kannada, Hindi and English (or a mix inside one
# conversation) all come back as ENGLISH text, so the rules, the filters and the cards only ever see English and the
# doctor never sees Kannada or Hindi text. The language that was heard is kept as a short code for the page.
# ("transcribe" would give the words in the language spoken; it is only a fallback for a speech service that has no
# translate mode.)
LIVE_STT_MODE = os.getenv("LIVE_STT_MODE", "translate").strip().lower() or "translate"


@dataclass
class LiveSession:
    consultation_id: str
    appointment_id: str | None = None
    history_lines: list[str] = field(default_factory=list)
    turns: list[dict] = field(default_factory=list)
    shown_texts: list[str] = field(default_factory=list)
    fired_rules: set[str] = field(default_factory=set)
    token: str | None = None            # the doctor's token, kept in memory only, to store cards
    language_words: dict = field(default_factory=dict)   # words heard per language ("kn", "hi", "en"...): counts only, never text
    trailing_silence: float = 0.0
    words_since_run: int = 0
    last_run: float = 0.0
    running: bool = False
    timer_pending: bool = False
    paused: bool = False
    error: str | None = None
    chunks: int = 0
    seconds: float = 0.0
    created: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_sessions: dict[str, LiveSession] = {}
_registry_lock = threading.Lock()


# --------------------------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------------------------

def _drop_stale(now: float) -> None:
    for key in [k for k, s in _sessions.items() if now - s.last_seen > SESSION_TTL_SECONDS]:
        _sessions.pop(key, None)


def get_session(consultation_id: str) -> LiveSession | None:
    with _registry_lock:
        return _sessions.get(consultation_id)


def open_session(consultation_id: str, appointment_id: str | None, history_lines: list[str], token: str) -> LiveSession:
    with _registry_lock:
        now = time.monotonic()
        _drop_stale(now)
        session = _sessions.get(consultation_id)
        if session is None:
            if len(_sessions) >= MAX_SESSIONS:
                oldest = min(_sessions.values(), key=lambda s: s.last_seen)
                _sessions.pop(oldest.consultation_id, None)
            session = LiveSession(consultation_id, appointment_id, list(history_lines))
            _sessions[consultation_id] = session
        session.token = token
        session.last_seen = now
        return session


def close_session(consultation_id: str) -> bool:
    with _registry_lock:
        return _sessions.pop(consultation_id, None) is not None


def reset_all() -> None:
    """For tests."""
    with _registry_lock:
        _sessions.clear()


# --------------------------------------------------------------------------------------------
# audio in
# --------------------------------------------------------------------------------------------

def _speaker_for(session: LiveSession, pause: float) -> str:
    """Same guess as the post-visit labelling: a long pause hands the turn to the other person."""
    if not session.turns:
        return "doctor"
    last = session.turns[-1]["speaker"]
    if pause >= transcribe.TURN_CHANGE_PAUSE_SECONDS:
        return "patient" if last == "doctor" else "doctor"
    return last


def ingest(session: LiveSession, wav: bytes, transcribe_many=None) -> int:
    """Transcribe one audio piece and add it to the conversation. Returns the number of new words.

    Raises audio.AudioError for an unreadable piece and transcribe.SttError if speech-to-text keeps failing.
    """
    transcribe_many = transcribe_many or (lambda segs: transcribe.transcribe_segments(segs, mode=LIVE_STT_MODE))
    seconds = audio.wav_seconds(wav)
    if seconds > MAX_CHUNK_SECONDS:
        raise audio.AudioError("That audio piece is too long.")
    segments = audio.split_on_speech(wav)

    with session.lock:
        session.chunks += 1
        session.seconds += seconds
        session.last_seen = time.monotonic()
        if not segments:
            session.trailing_silence += seconds
            return 0

    results = transcribe_many(segments)          # (outside the lock: it is a network call)

    added = 0
    with session.lock:
        previous_end = -session.trailing_silence    # so the first pause includes silence from the last piece
        for segment, result in zip(segments, results):
            text = (result.get("text") or "").strip()
            if not text:
                continue                             # noise: counts as silence
            code = str(result.get("language_code") or "").split("-")[0].lower()
            if code and code != "unknown":
                session.language_words[code] = session.language_words.get(code, 0) + len(text.split())
            speaker = _speaker_for(session, segment.start - previous_end)
            previous_end = segment.end
            if session.turns and session.turns[-1]["speaker"] == speaker:
                session.turns[-1]["text"] += " " + text
            else:
                session.turns.append({"speaker": speaker, "text": text, "language_code": result.get("language_code")})
            added += len(text.split())
        session.trailing_silence = max(0.0, seconds - previous_end) if previous_end > 0 else session.trailing_silence + seconds
        session.words_since_run += added
        session.error = None if session.error == "speech_unavailable" else session.error
    return added


# --------------------------------------------------------------------------------------------
# suggestions out
# --------------------------------------------------------------------------------------------

def transcript_text(session: LiveSession) -> str:
    labels = {"doctor": "Doctor", "patient": "Patient"}
    return "\n".join(f"{labels.get(t['speaker'], 'Speaker')}: {t['text']}" for t in session.turns)


def ready_for_engine(session: LiveSession, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    with session.lock:
        return (
            not session.paused
            and not session.running
            and session.words_since_run >= MIN_NEW_WORDS
            and (session.last_run == 0.0 or now - session.last_run >= ENGINE_MIN_INTERVAL_SECONDS)
        )


def claim_engine_run(session: LiveSession, now: float | None = None) -> bool:
    """Atomically decide that THIS caller runs the engine (so two chunks never start two runs)."""
    now = time.monotonic() if now is None else now
    with session.lock:
        if (
            session.paused or session.running or session.words_since_run < MIN_NEW_WORDS
            or (session.last_run and now - session.last_run < ENGINE_MIN_INTERVAL_SECONDS)
        ):
            return False
        session.running = True
        session.last_run = now
        session.words_since_run = 0
        return True


def immediate_red_flags(session: LiveSession) -> list[dict]:
    """Built-in red-flag rules, checked on every audio piece (no waiting for the model or the interval).

    Returns only cards that have not fired before in this consultation.
    """
    with session.lock:
        if session.paused:
            return []
        text = live_assist.statements(transcript_text(session)[-2500:])
        cards = live_assist.red_flag_cards(text, session.fired_rules)
        session.shown_texts.extend(c["text"] for c in cards)
    return cards


def schedule_deferred_run(session: LiveSession, store_cards, chat=None, now: float | None = None) -> bool:
    """New speech arrived but the interval has not passed: make sure it is not forgotten.

    One timer at most is pending per session. When it fires, it runs the engine if there is still enough new
    speech. Returns True if a timer was scheduled.
    """
    now = time.monotonic() if now is None else now
    with session.lock:
        if session.paused or session.timer_pending or session.words_since_run < MIN_NEW_WORDS:
            return False
        delay = max(0.5, (session.last_run + ENGINE_MIN_INTERVAL_SECONDS) - now) if session.last_run else 0.5
        if session.running:
            delay = max(delay, 2.0)
        session.timer_pending = True

    def fire():
        with session.lock:
            session.timer_pending = False
        if claim_engine_run(session):
            run_engine(session, store_cards, chat=chat)
        else:
            schedule_deferred_run(session, store_cards, chat=chat)      # still running or too soon: try again

    timer = threading.Timer(delay, fire)
    timer.daemon = True
    timer.start()
    return True


def run_engine(session: LiveSession, store_cards, chat=None) -> list[dict]:
    """One suggestion pass: built-in red-flag rules plus the model. `store_cards(cards)` saves them.

    The caller must have won claim_engine_run(). Never raises.
    """
    stored: list[dict] = []
    try:
        with session.lock:
            transcript = transcript_text(session)
            history = list(session.history_lines)
            shown = list(session.shown_texts)
            fired = session.fired_rules

        cards = live_assist.red_flag_cards(live_assist.statements(transcript[-2500:]), fired)
        model_error = None
        try:
            model_cards, dropped = live_assist.generate(transcript, history, shown + [c["text"] for c in cards], chat=chat)
            if dropped:
                print(f"[LiveAssist] {session.consultation_id}: dropped {dropped}")
            cards += model_cards
        except summarize.SummaryError as exc:
            model_error = exc.code
            print(f"[LiveAssist] {session.consultation_id}: model unavailable ({exc})")

        if cards:
            stored = store_cards(cards[: MAX_CARDS_VISIBLE]) or []
            with session.lock:
                session.shown_texts.extend(c["text"] for c in cards)
        with session.lock:
            session.error = model_error and "suggestions_unavailable"
    except Exception as exc:  # noqa: BLE001 - a live helper must never take anything else down
        print(f"[LiveAssist] {session.consultation_id}: engine error {exc!r}")
        with session.lock:
            session.error = "suggestions_unavailable"
    finally:
        with session.lock:
            session.running = False
    return stored


LANGUAGE_SHARE = 0.2     # a language must be this share of the words to be shown (a mis-detected short piece is not)


def _languages_heard(session: LiveSession) -> list[str]:
    """The languages that made up the conversation so far, most spoken first, e.g. ["kn", "en"]."""
    counts = {k: v for k, v in session.language_words.items() if v > 0}
    total = sum(counts.values())
    if not total:
        return []
    ordered = sorted(counts, key=lambda k: -counts[k])
    return [k for k in ordered if counts[k] / total >= LANGUAGE_SHARE] or ordered[:1]


def snapshot(session: LiveSession) -> dict:
    with session.lock:
        state = "paused" if session.paused else ("error" if session.error else "listening")
        return {
            "state": state,
            "error": session.error,
            "languages": _languages_heard(session),
            "chunks": session.chunks,
            "seconds": round(session.seconds, 1),
            "turns": [dict(t) for t in session.turns[-TRANSCRIPT_TAIL_TURNS:]],
            "turn_count": len(session.turns),
        }
