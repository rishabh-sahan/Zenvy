"""Live Assist: the suggestion engine's checks, the live session, and the gateway endpoints."""
import io
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.agents import live_assist
from services.gateway import doctor_routes
from services.scribe import audio, live, summarize, transcribe
from services.scribe.team_c import TeamCError
from tests.test_scribe import quiet, tone, wav_of


@pytest.fixture(autouse=True)
def no_real_model_for_medicines(monkeypatch):
    """The medicine reading also uses the language model; these tests never call the real one."""
    def down(messages):
        raise summarize.SummaryError("summary_failed", "no model in tests")
    monkeypatch.setattr(summarize, "call_chat", down)

# ---------------------------------------------------------------------------
# what the doctor must never be shown
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,reason", [
    ("Consider starting paracetamol 500 mg.", "dose"),
    ("Suggest 10 ml of the syrup twice a day.", "dose"),
    ("The patient could take ibuprofen.", "medicine name"),
    ("Consider an antibiotic course.", "treatment"),
    ("Order a CBC and an x-ray.", "test"),
    ("Consider an MRI of the head.", "test"),
    ("This could be dengue.", "disease name"),
    ("Possible diabetes, keep in mind.", "disease name"),
    ("The patient has pneumonia.", "disease name"),
    ("It is a viral infection.", "diagnosis"),
    ("The diagnosis is clear.", "diagnosis"),
])
def test_unsafe_card_text_is_recognised(text, reason):
    assert reason in live_assist.violations(text) or live_assist.violations(text), text


@pytest.mark.parametrize("text", [
    "Ask how long the fever has lasted.",
    "Ask whether the patient has any allergies.",
    "Consider asking about recent travel.",
    "Keep in mind the earlier visit on 2 Oct for fever.",
    "Follow-up date has not been discussed yet.",
    "Chest pain was mentioned - consider urgent evaluation.",
])
def test_safe_card_text_passes(text):
    assert live_assist.violations(text) == []


# ---------------------------------------------------------------------------
# built-in red-flag rules
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said,key", [
    ("I have chest pain since morning", "chest_pain"),
    ("there is a pain in my chest", "chest_pain"),
    ("I am short of breath when I walk", "breathing"),
    ("he is having difficulty breathing", "breathing"),
    ("she fainted yesterday", "fainting"),
    ("he had a seizure last night", "seizure"),
    ("I am vomiting blood", "bleeding"),
    ("sometimes I want to die", "self_harm"),
    ("my speech became slurred speech suddenly", "stroke_signs"),
    ("this is the worst headache of my life", "severe_headache"),
    ("there is swelling of the face", "swelling"),
])
def test_red_flag_phrases_fire_once(said, key):
    fired: set[str] = set()
    first = live_assist.red_flag_cards(said, fired)
    assert [c["kind"] for c in first] == ["red_flag"] and first[0]["source"] == "rule" and key in fired
    assert "urgent evaluation" in first[0]["text"] and first[0]["evidence"]
    assert live_assist.red_flag_cards(said + " again", fired) == []          # only once per consultation


@pytest.mark.parametrize("said", [
    "no chest pain", "I do not have any chest pain", "without difficulty breathing", "never fainted",
    "denies seizures", "I am fit and healthy", "the fever started yesterday",
])
def test_denied_or_unrelated_phrases_do_not_fire(said):
    assert live_assist.red_flag_cards(said, set()) == []


# ---------------------------------------------------------------------------
# reading and checking the model's cards
# ---------------------------------------------------------------------------

TRANSCRIPT = ("Doctor: What brings you in today?\nPatient: I have had a fever and a sore throat for three days. "
              "My body aches and I feel very tired.\nDoctor: Any cough?\nPatient: No cough but my chest feels heavy.")
HISTORY = "2026-10-02: complaint: fever; assessment: viral; plan: rest; medicines: Paracetamol 500 mg"


def card(kind="ask", text="Ask whether anyone at home has similar symptoms.", evidence="body aches and I feel very tired"):
    return {"kind": kind, "text": text, "evidence": evidence}


def test_a_grounded_safe_card_is_kept():
    kept, dropped = live_assist.check_cards([card()], TRANSCRIPT, HISTORY, [])
    assert len(kept) == 1 and dropped == {}
    assert kept[0]["source"] == "model" and kept[0]["kind"] == "ask"


def test_a_card_quoting_something_never_said_is_dropped():
    kept, dropped = live_assist.check_cards([card(evidence="I have been travelling to Goa")], TRANSCRIPT, HISTORY, [])
    assert kept == [] and dropped == {"not_grounded": 1}


def test_a_card_with_no_evidence_is_dropped_except_wrap_up():
    assert live_assist.check_cards([card(evidence="")], TRANSCRIPT, HISTORY, [])[0] == []
    wrap = {"kind": "wrap_up", "text": "How long to continue has not been discussed.", "evidence": ""}
    assert len(live_assist.check_cards([wrap], TRANSCRIPT, HISTORY, [])[0]) == 1


def test_unsafe_cards_are_dropped_with_the_reason():
    cards = [card(text="Consider paracetamol 500 mg for the fever."), card(text="Order a blood test today please."),
             card(text="This could be dengue, keep it in mind.")]
    kept, dropped = live_assist.check_cards(cards, TRANSCRIPT, HISTORY, [])
    assert kept == [] and sum(dropped.values()) == 3 and all(k.startswith("unsafe:") for k in dropped)


def test_a_history_card_must_quote_the_earlier_visit_not_the_conversation():
    ok = {"kind": "history", "text": "Earlier visit on 2 Oct was for fever.", "evidence": "2026-10-02 fever viral"}
    bad = {"kind": "history", "text": "You saw them for a rash before.", "evidence": "rash last year"}
    kept, dropped = live_assist.check_cards([ok, bad], TRANSCRIPT, HISTORY, [])
    assert [c["kind"] for c in kept] == ["history"] and dropped == {"not_grounded": 1}


def test_repeats_of_what_was_already_shown_are_dropped():
    shown = ["Ask whether anyone at home has similar symptoms."]
    again = card(text="Ask if anyone at home has similar symptoms?")
    assert live_assist.check_cards([again], TRANSCRIPT, HISTORY, shown)[0] == []
    two = [card(), card(text="Ask whether anyone at home has similar symptoms too.")]
    assert len(live_assist.check_cards(two, TRANSCRIPT, HISTORY, [])[0]) == 1


def test_a_consider_card_always_starts_softly():
    soft = card(kind="consider", text="The chest heaviness may matter with the fever.", evidence="my chest feels heavy")
    kept, _ = live_assist.check_cards([soft], TRANSCRIPT, HISTORY, [])
    assert kept[0]["text"].startswith("Keep in mind: the chest")
    already = card(kind="consider", text="Consider the chest heaviness together with the fever.", evidence="my chest feels heavy")
    assert live_assist.check_cards([already], TRANSCRIPT, HISTORY, [])[0][0]["text"].startswith("Consider")


def test_odd_cards_are_dropped_and_at_most_four_are_kept():
    odd = [{"kind": "diagnose", "text": "x" * 20}, {"kind": "ask", "text": "short"}, {"kind": "ask", "text": "y" * 300},
           "not a dict"]
    kept, dropped = live_assist.check_cards([c for c in odd if isinstance(c, dict)], TRANSCRIPT, HISTORY, [])
    assert kept == [] and dropped == {"unknown_kind": 1, "bad_length": 2}
    topics = ["travel", "allergies", "sleep", "appetite", "weight", "stress", "smoking", "alcohol", "exercise"]
    many = [card(text=f"Ask the patient about their {topic} recently.") for topic in topics]
    assert len(live_assist.check_cards(many, TRANSCRIPT, HISTORY, [])[0]) == live_assist.MAX_CARDS_PER_RUN


@pytest.mark.parametrize("reply", [
    '{"cards": [{"kind": "ask", "text": "Ask about allergies please.", "evidence": "sore throat"}]}',
    '<think>hmm</think>```json\n{"cards": []}\n```',
    'Sure! {"cards": []} hope that helps',
])
def test_the_models_reply_is_read_tolerantly(reply):
    assert isinstance(live_assist.parse_cards(reply), list)


@pytest.mark.parametrize("reply", ["", "no cards today", '{"cards": "none"}', "{broken"])
def test_an_unusable_reply_is_an_error(reply):
    with pytest.raises(Exception):
        live_assist.parse_cards(reply)


def test_generate_asks_the_model_with_history_and_what_was_already_shown():
    seen = {}

    def chat(messages):
        seen["system"], seen["user"] = messages[0]["content"], messages[1]["content"]
        return json.dumps({"cards": [card()]})

    cards, dropped = live_assist.generate(TRANSCRIPT, [HISTORY], ["Ask about allergies."], chat=chat)
    assert len(cards) == 1
    assert "NEVER state or imply a diagnosis" in seen["system"] and "ALREADY SHOWN" in seen["system"]
    assert "fever and a sore throat" in seen["user"] and "2026-10-02" in seen["user"] and "Ask about allergies." in seen["user"]


def test_generate_turns_a_bad_reply_into_a_summary_error():
    with pytest.raises(summarize.SummaryError):
        live_assist.generate(TRANSCRIPT, [], [], chat=lambda m: "I cannot do that")


# ---------------------------------------------------------------------------
# the live session
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_sessions():
    live.reset_all()
    STT_CALLS.clear()
    yield
    live.reset_all()


def speech_chunk(*pieces):
    """A WAV made of ('talk', seconds) / ('quiet', seconds) pieces."""
    parts = [tone(sec) if kind == "talk" else quiet(sec) for kind, sec in pieces]
    return wav_of(*parts)


STT_CALLS: list[dict] = []        # the extra arguments every fake speech-to-text call received


def fake_stt(*texts, language="en-IN"):
    queue = list(texts)

    def run(segments, **kwargs):
        STT_CALLS.append(kwargs)
        return [{"text": queue.pop(0) if queue else "", "language_code": language} for _ in segments]

    return run


def test_speech_becomes_turns_and_a_long_pause_changes_speaker():
    s = live.open_session("c1", "a1", [], "tok")
    wav = speech_chunk(("quiet", 0.3), ("talk", 1.5), ("quiet", 1.6), ("talk", 1.5), ("quiet", 0.3))
    added = live.ingest(s, wav, fake_stt("What is troubling you today", "I have a fever and a headache"))
    assert [t["speaker"] for t in s.turns] == ["doctor", "patient"]
    assert added == 5 + 7 and s.words_since_run == 12


def test_a_short_pause_keeps_the_same_speaker():
    s = live.open_session("c1", "a1", [], "tok")
    wav = speech_chunk(("talk", 1.2), ("quiet", 0.7), ("talk", 1.2))
    live.ingest(s, wav, fake_stt("I have had a fever", "and a sore throat"))
    assert len(s.turns) == 1 and s.turns[0]["text"] == "I have had a fever and a sore throat"


def test_the_pause_between_two_audio_pieces_counts_for_the_speaker_change():
    s = live.open_session("c1", "a1", [], "tok")
    live.ingest(s, speech_chunk(("talk", 1.5), ("quiet", 1.0)), fake_stt("Any allergies to anything"))
    live.ingest(s, speech_chunk(("quiet", 0.6), ("talk", 1.5)), fake_stt("No I have none"))
    assert [t["speaker"] for t in s.turns] == ["doctor", "patient"]            # 1.0 + 0.6 s of silence in between


def test_a_continuous_sentence_across_pieces_stays_with_one_speaker():
    s = live.open_session("c1", "a1", [], "tok")
    live.ingest(s, speech_chunk(("talk", 1.5), ("quiet", 0.1)), fake_stt("I have had a fever"))
    live.ingest(s, speech_chunk(("quiet", 0.1), ("talk", 1.5)), fake_stt("since three days ago"))
    assert len(s.turns) == 1


def test_silence_noise_and_empty_text_add_nothing():
    s = live.open_session("c1", "a1", [], "tok")
    assert live.ingest(s, wav_of(quiet(2.0)), fake_stt()) == 0
    assert live.ingest(s, speech_chunk(("talk", 1.0)), fake_stt("")) == 0
    assert s.turns == [] and s.words_since_run == 0 and s.chunks == 2


def test_an_audio_piece_that_is_too_long_or_unreadable_is_refused():
    s = live.open_session("c1", "a1", [], "tok")
    with pytest.raises(audio.AudioError):
        live.ingest(s, wav_of(quiet(live.MAX_CHUNK_SECONDS + 5)), fake_stt())
    with pytest.raises(Exception):
        live.ingest(s, b"not audio at all", fake_stt())


def test_the_transcript_is_labelled_for_the_model():
    s = live.open_session("c1", "a1", [], "tok")
    s.turns += [{"speaker": "doctor", "text": "Hello"}, {"speaker": "patient", "text": "Hi doctor"}, {"speaker": "unknown", "text": "..."}]
    assert live.transcript_text(s) == "Doctor: Hello\nPatient: Hi doctor\nSpeaker: ..."


# ---------------------------------------------------------------------------
# when the engine runs
# ---------------------------------------------------------------------------

def test_the_engine_waits_for_enough_new_speech_and_for_the_interval():
    s = live.open_session("c1", "a1", [], "tok")
    s.words_since_run = live.MIN_NEW_WORDS - 1
    assert live.claim_engine_run(s, now=100.0) is False                    # too little new speech
    s.words_since_run = live.MIN_NEW_WORDS
    assert live.claim_engine_run(s, now=100.0) is True
    assert s.running and s.words_since_run == 0
    s.running = False
    s.words_since_run = 50
    assert live.claim_engine_run(s, now=100.0 + live.ENGINE_MIN_INTERVAL_SECONDS - 1) is False   # too soon
    assert live.claim_engine_run(s, now=100.0 + live.ENGINE_MIN_INTERVAL_SECONDS + 1) is True


def test_only_one_run_at_a_time_even_with_many_callers():
    s = live.open_session("c1", "a1", [], "tok")
    s.words_since_run = 100
    wins = []
    barrier = threading.Barrier(12)

    def attempt():
        barrier.wait()
        wins.append(live.claim_engine_run(s, now=500.0))

    threads = [threading.Thread(target=attempt) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert wins.count(True) == 1


def test_a_paused_session_never_runs_the_engine():
    s = live.open_session("c1", "a1", [], "tok")
    s.words_since_run = 100
    s.paused = True
    assert live.claim_engine_run(s, now=500.0) is False
    assert live.ready_for_engine(s, now=500.0) is False


# ---------------------------------------------------------------------------
# a run of the engine
# ---------------------------------------------------------------------------

def make_session_with(text_turns):
    s = live.open_session("c1", "a1", [HISTORY], "tok")
    s.turns = [{"speaker": sp, "text": tx} for sp, tx in text_turns]
    s.words_since_run = 100
    return s


def test_a_run_stores_the_safe_cards_and_remembers_them():
    s = make_session_with([("patient", "I have had a fever and a sore throat for three days and my body aches")])
    stored = []
    assert live.claim_engine_run(s, now=1.0)
    chat = lambda m: json.dumps({"cards": [
        {"kind": "ask", "text": "Ask whether the fever comes with chills.", "evidence": "fever and a sore throat"},
        {"kind": "ask", "text": "Consider paracetamol 500 mg now.", "evidence": "fever"},       # unsafe: dropped
    ]})
    live.run_engine(s, lambda cards: stored.extend(cards) or cards, chat=chat)
    assert [c["text"] for c in stored] == ["Ask whether the fever comes with chills."]
    assert s.shown_texts == ["Ask whether the fever comes with chills."] and s.running is False and s.error is None


def test_a_red_flag_card_appears_even_if_the_model_has_nothing():
    s = make_session_with([("patient", "I have chest pain since this morning")])
    stored = []
    assert live.claim_engine_run(s, now=1.0)
    live.run_engine(s, lambda cards: stored.extend(cards) or cards, chat=lambda m: json.dumps({"cards": []}))
    assert [c["kind"] for c in stored] == ["red_flag"] and stored[0]["source"] == "rule"


def test_a_red_flag_still_appears_when_the_model_is_down():
    s = make_session_with([("patient", "I fainted twice yesterday")])
    stored = []

    def down(messages):
        raise summarize.SummaryError("summary_failed", "model down")

    assert live.claim_engine_run(s, now=1.0)
    live.run_engine(s, lambda cards: stored.extend(cards) or cards, chat=down)
    assert [c["kind"] for c in stored] == ["red_flag"]
    assert s.error == "suggestions_unavailable" and s.running is False


def test_a_failure_to_store_cards_never_escapes_and_releases_the_engine():
    s = make_session_with([("patient", "I have a fever and my body aches a lot today")])

    def explode(cards):
        raise TeamCError(500, "database down")

    assert live.claim_engine_run(s, now=1.0)
    chat = lambda m: json.dumps({"cards": [{"kind": "ask", "text": "Ask about chills and sweating.", "evidence": "fever and my body aches"}]})
    live.run_engine(s, explode, chat=chat)               # must not raise
    assert s.running is False and s.error == "suggestions_unavailable"


def test_the_snapshot_reports_the_state():
    s = live.open_session("c1", "a1", [], "tok")
    s.turns = [{"speaker": "doctor", "text": str(n)} for n in range(30)]
    assert live.snapshot(s)["state"] == "listening" and len(live.snapshot(s)["turns"]) == live.TRANSCRIPT_TAIL_TURNS
    s.error = "speech_unavailable"
    assert live.snapshot(s)["state"] == "error"
    s.paused = True
    assert live.snapshot(s)["state"] == "paused"


def test_old_sessions_are_forgotten_and_the_registry_is_bounded():
    for n in range(live.MAX_SESSIONS + 5):
        live.open_session(f"c{n}", None, [], "tok")
    assert len(live._sessions) == live.MAX_SESSIONS
    stale = live.open_session("old", None, [], "tok")
    stale.last_seen -= live.SESSION_TTL_SECONDS + 10
    live.open_session("trigger", None, [], "tok")
    assert live.get_session("old") is None


# ---------------------------------------------------------------------------
# the gateway endpoints
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(doctor_routes.router)
client = TestClient(app)
AUTH = {"Authorization": "Bearer doctor-token"}


class FakeTeamC:
    consent = "granted"
    error = None
    cards: list = []
    stored: list = []
    history = [{"date": "2026-10-02", "chief_complaint": "fever", "assessment": "viral", "plan": "rest", "medicines": ["Paracetamol 500 mg"]}]

    def __init__(self, token):
        self.token = token

    def get_consultation(self, consultation_id):
        if FakeTeamC.error:
            raise FakeTeamC.error
        return {"consultation_id": consultation_id, "appointment_id": "a1", "consent_state": FakeTeamC.consent, "turns": []}

    def patient_history(self, appointment_id):
        return FakeTeamC.history

    def list_live_suggestions(self, consultation_id, after=0):
        if FakeTeamC.error:
            raise FakeTeamC.error
        return FakeTeamC.cards

    def add_live_suggestions(self, consultation_id, cards):
        FakeTeamC.stored.extend(cards)
        return cards


@pytest.fixture
def api(monkeypatch):
    FakeTeamC.consent, FakeTeamC.error, FakeTeamC.cards, FakeTeamC.stored = "granted", None, [], []
    monkeypatch.setattr(doctor_routes, "TeamC", FakeTeamC)
    monkeypatch.setattr(doctor_routes, "LIVE_ASSIST_ENABLED", True)
    monkeypatch.setattr(live.transcribe, "transcribe_segments", fake_stt("Tell me about the fever", "It started three days ago"))
    return FakeTeamC


def post_chunk(consultation="c1", data=None, headers=AUTH):
    data = data if data is not None else speech_chunk(("quiet", 0.3), ("talk", 1.5), ("quiet", 1.4), ("talk", 1.5))
    return client.post(f"/doctor/api/consultations/{consultation}/live/audio", headers=headers,
                       files={"file": ("chunk.wav", data, "audio/wav")})


def test_the_config_says_whether_live_assist_is_on(api, monkeypatch):
    body = client.get("/doctor/api/live/config").json()
    assert body == {"enabled": True, "chunk_seconds": live.CHUNK_SECONDS, "visible_cards": live.MAX_CARDS_VISIBLE}
    monkeypatch.setattr(doctor_routes, "LIVE_ASSIST_ENABLED", False)
    assert client.get("/doctor/api/live/config").json()["enabled"] is False


def test_when_switched_off_every_live_route_is_404(api, monkeypatch):
    monkeypatch.setattr(doctor_routes, "LIVE_ASSIST_ENABLED", False)
    assert post_chunk().status_code == 404
    assert client.get("/doctor/api/consultations/c1/live", headers=AUTH).status_code == 404
    assert client.post("/doctor/api/consultations/c1/live/pause", headers=AUTH, json={"paused": True}).status_code == 404
    assert client.post("/doctor/api/consultations/c1/live/end", headers=AUTH).status_code == 404


def test_every_live_route_needs_a_login(api):
    assert post_chunk(headers={}).status_code == 401
    assert client.get("/doctor/api/consultations/c1/live").status_code == 401
    assert client.post("/doctor/api/consultations/c1/live/pause", json={"paused": True}).status_code == 401
    assert client.post("/doctor/api/consultations/c1/live/end").status_code == 401


def test_a_piece_becomes_transcript_and_the_session_opens_with_history(api, monkeypatch):
    monkeypatch.setattr(live, "run_engine", lambda *a, **k: [])
    body = post_chunk().json()
    assert body["ok"] is True and body["state"] == "listening" and body["added_words"] == 10
    session = live.get_session("c1")
    assert [t["speaker"] for t in session.turns] == ["doctor", "patient"]
    assert session.history_lines and session.history_lines[0].startswith("2026-10-02: complaint: fever")
    assert "medicines: Paracetamol 500 mg" in session.history_lines[0] and session.token == "doctor-token"


def test_the_engine_is_started_in_the_background_once_there_is_enough_speech(api, monkeypatch):
    started = threading.Event()
    seen = {}

    def fake_run(session, store, chat=None):
        seen["consultation"] = session.consultation_id
        store([{"kind": "ask", "text": "Ask about chills.", "evidence": "fever"}])
        started.set()
        session.running = False

    monkeypatch.setattr(live, "run_engine", fake_run)
    post_chunk()
    assert started.wait(5) and seen["consultation"] == "c1"
    assert FakeTeamC.stored == [{"kind": "ask", "text": "Ask about chills.", "evidence": "fever"}]


def test_a_missing_consent_stops_everything(api):
    FakeTeamC.consent = "none"
    response = post_chunk()
    assert response.status_code == 409 and response.json()["detail"] == "consent_required"
    assert live.get_session("c1") is None
    FakeTeamC.consent = "declined"
    assert post_chunk().status_code == 409


def test_a_consultation_that_is_not_the_doctors_is_refused(api):
    FakeTeamC.error = TeamCError(403, "This is not your consultation")
    assert post_chunk().status_code == 403
    assert client.get("/doctor/api/consultations/c1/live", headers=AUTH).status_code == 403
    assert client.post("/doctor/api/consultations/c1/live/pause", headers=AUTH, json={"paused": True}).status_code == 403
    assert client.post("/doctor/api/consultations/c1/live/end", headers=AUTH).status_code == 403


def test_bad_audio_pieces_are_refused_clearly(api):
    assert client.post("/doctor/api/consultations/c1/live/audio", headers=AUTH, files={"file": ("c.wav", b"", "audio/wav")}).status_code == 422
    big = b"RIFF" + b"\0" * (live.MAX_CHUNK_BYTES + 10)
    assert post_chunk(data=big).status_code == 413
    assert post_chunk(data=b"RIFFnotreallyawavefile").status_code == 422


def test_a_speech_to_text_failure_is_reported_but_never_an_error_status(api, monkeypatch):
    def broken(segments, **kwargs):
        raise transcribe.SttError("HTTP 502")

    monkeypatch.setattr(live.transcribe, "transcribe_segments", broken)
    response = post_chunk()
    assert response.status_code == 200 and response.json() == {"ok": False, "state": "error", "error": "speech_unavailable", "added_words": 0}
    assert client.get("/doctor/api/consultations/c1/live", headers=AUTH).json()["state"] == "error"


def test_pausing_stops_listening_and_resuming_continues(api, monkeypatch):
    monkeypatch.setattr(live, "run_engine", lambda *a, **k: [])
    post_chunk()
    paused = client.post("/doctor/api/consultations/c1/live/pause", headers=AUTH, json={"paused": True}).json()
    assert paused == {"paused": True, "state": "paused"}
    before = live.get_session("c1").chunks
    assert post_chunk().json() == {"ok": True, "state": "paused", "added_words": 0}
    assert live.get_session("c1").chunks == before                            # the piece was not even transcribed
    client.post("/doctor/api/consultations/c1/live/pause", headers=AUTH, json={"paused": False})
    assert post_chunk().json()["state"] == "listening"


def test_the_panel_state_has_cards_lines_and_the_visible_limit(api, monkeypatch):
    monkeypatch.setattr(live, "run_engine", lambda *a, **k: [])
    FakeTeamC.cards = [{"suggestion_id": "s1", "seq": 1, "kind": "ask", "text": "Ask about chills.", "evidence": "fever", "source": "model", "status": "shown"}]
    post_chunk()
    state = client.get("/doctor/api/consultations/c1/live", headers=AUTH).json()
    assert state["enabled"] and state["state"] == "listening" and state["visible_cards"] == live.MAX_CARDS_VISIBLE
    assert [t["speaker"] for t in state["turns"]] == ["doctor", "patient"] and state["suggestions"] == FakeTeamC.cards


def test_before_any_audio_the_state_is_idle(api):
    state = client.get("/doctor/api/consultations/c1/live", headers=AUTH).json()
    assert state["state"] == "idle" and state["turns"] == [] and state["suggestions"] == []


def test_ending_forgets_the_conversation_but_not_the_cards(api, monkeypatch):
    monkeypatch.setattr(live, "run_engine", lambda *a, **k: [])
    FakeTeamC.cards = [{"suggestion_id": "s1", "seq": 1, "kind": "ask", "text": "t", "status": "shown", "source": "model"}]
    post_chunk()
    assert client.post("/doctor/api/consultations/c1/live/end", headers=AUTH).json() == {"ended": True, "medicines": []}
    assert live.get_session("c1") is None
    assert client.post("/doctor/api/consultations/c1/live/end", headers=AUTH).json() == {"ended": False, "medicines": []}
    assert client.get("/doctor/api/consultations/c1/live", headers=AUTH).json()["suggestions"] == FakeTeamC.cards


def test_the_card_action_route_is_an_allowed_pass_through(monkeypatch):
    import requests

    seen = {}

    class Up:
        status_code, content, headers = 200, b'{"status":"dismissed"}', {"content-type": "application/json"}

    def fake_request(method, url, params=None, data=None, headers=None, timeout=None):
        seen.update(method=method, url=url)
        return Up()

    monkeypatch.setattr(requests, "request", fake_request)
    response = client.patch("/doctor/api/consultations/c1/live/suggestions/s1", headers=AUTH, json={"status": "dismissed"})
    assert response.status_code == 200 and seen["url"].endswith("/api/v1/consultations/c1/live/suggestions/s1")
    # ... but a doctor page cannot write cards itself, or read the list through the proxy
    seen.clear()
    assert client.post("/doctor/api/consultations/c1/live/suggestions", headers=AUTH, json={"cards": []}).status_code == 404
    assert "url" not in seen


# ---------------------------------------------------------------------------
# questions are not symptoms; red flags are immediate; leftover speech is not forgotten
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said", [
    "Any cough or difficulty breathing?",
    "Do you have any chest pain?",
    "Have you fainted recently?",
    "Is there any chest pain when you walk?",
    "Tell me if you feel short of breath",
    "Did you have a seizure before?",
])
def test_a_doctors_question_is_not_a_red_flag(said):
    assert live_assist.red_flag_cards(live_assist.statements(said), set()) == []


def test_a_patients_statement_in_the_same_conversation_still_fires():
    said = "Any cough or difficulty breathing? No cough. I have chest pain since this morning."
    cards = live_assist.red_flag_cards(live_assist.statements(said), set())
    assert [c["evidence"].lower() for c in cards] == ["chest pain"]


def test_the_sample_consultation_does_not_raise_a_false_alarm():
    said = ("Good morning. What brings you in today? Good morning doctor. I have had a fever and a sore throat for "
            "three days. Any cough or difficulty breathing? No cough, but I feel very tired and my body aches.")
    assert live_assist.red_flag_cards(live_assist.statements(said), set()) == []


def test_red_flags_are_checked_on_every_piece_without_waiting():
    s = live.open_session("c1", "a1", [], "tok")
    s.turns = [{"speaker": "patient", "text": "I have chest pain since this morning"}]
    first = live.immediate_red_flags(s)
    assert [c["kind"] for c in first] == ["red_flag"] and s.shown_texts == [first[0]["text"]]
    assert live.immediate_red_flags(s) == []                           # only once
    s.paused = True
    s.turns.append({"speaker": "patient", "text": "and I fainted"})
    assert live.immediate_red_flags(s) == []                           # nothing while paused


def test_leftover_speech_triggers_a_later_run_instead_of_being_forgotten():
    s = live.open_session("c1", "a1", [], "tok")
    s.turns = [{"speaker": "patient", "text": "I have a fever and my body aches a lot since yesterday evening"}]
    s.words_since_run = 20
    s.last_run = time.monotonic()                                      # a run just happened: too soon now
    assert live.claim_engine_run(s) is False
    ran = threading.Event()
    stored = []

    def chat(messages):
        ran.set()
        return json.dumps({"cards": [{"kind": "ask", "text": "Ask about chills and sweating.", "evidence": "fever and my body aches"}]})

    original = live.ENGINE_MIN_INTERVAL_SECONDS
    live.ENGINE_MIN_INTERVAL_SECONDS = 1
    try:
        assert live.schedule_deferred_run(s, lambda cards: stored.extend(cards) or cards, chat=chat) is True
        assert live.schedule_deferred_run(s, lambda cards: cards, chat=chat) is False        # one timer at most
        assert ran.wait(6)
        deadline = time.monotonic() + 3
        while not stored and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        live.ENGINE_MIN_INTERVAL_SECONDS = original
    assert [c["text"] for c in stored] == ["Ask about chills and sweating."]


def test_no_deferred_run_when_there_is_too_little_new_speech_or_when_paused():
    s = live.open_session("c1", "a1", [], "tok")
    s.words_since_run = 2
    assert live.schedule_deferred_run(s, lambda c: c) is False
    s.words_since_run = 50
    s.paused = True
    assert live.schedule_deferred_run(s, lambda c: c) is False


def test_the_endpoint_stores_a_red_flag_straight_away(api, monkeypatch):
    monkeypatch.setattr(live, "run_engine", lambda *a, **k: [])
    monkeypatch.setattr(live.transcribe, "transcribe_segments", fake_stt("Good morning what troubles you", "I have chest pain since this morning"))
    post_chunk()
    assert [c["kind"] for c in FakeTeamC.stored] == ["red_flag"]
    assert FakeTeamC.stored[0]["source"] == "rule" and "chest pain" in FakeTeamC.stored[0]["evidence"].lower()


def test_speaker_labels_do_not_hide_a_question_from_the_red_flag_rules():
    """Found live: the labelled transcript starts every line with 'Doctor:' / 'Patient:'."""
    labelled = ("Doctor: Good morning.\nPatient: I have had a fever.\nDoctor: Any cough or difficulty breathing sleeping\n"
                "Patient: No cough, but I feel tired.")
    assert live_assist.red_flag_cards(live_assist.statements(labelled), set()) == []
    flagged = labelled + "\nPatient: I have chest pain since morning."
    assert [c["evidence"].lower() for c in live_assist.red_flag_cards(live_assist.statements(flagged), set())] == ["chest pain"]


def test_a_model_red_flag_without_urgency_is_downgraded_not_shown_as_a_red_flag():
    cards = [
        {"kind": "red_flag", "text": "Ask about any new or worsening symptoms given the fever.", "evidence": "fever"},
        {"kind": "red_flag", "text": "Keep in mind the patient has a fever of 101 degrees.", "evidence": "fever"},
        {"kind": "red_flag", "text": "Chest pain may need urgent attention.", "evidence": "chest pain"},
    ]
    kept, _ = live_assist.check_cards(cards, "I have chest pain and a fever of 101 degrees", "", [])
    assert [c["kind"] for c in kept] == ["ask", "consider", "red_flag"]


def test_a_statement_with_a_question_mark_from_speech_to_text_still_fires():
    """Found live: the speech service wrote "I have chest pain since this morning?"."""
    said = "Good morning doctor. I have chest pain since this morning? And I feel a little breathless."
    cards = live_assist.red_flag_cards(live_assist.statements(said), set())
    assert [c["evidence"].lower() for c in cards] == ["chest pain", "breathless"]


@pytest.mark.parametrize("said", [
    "Doctor, any chest pain?", "So do you have any chest pain", "Okay, have you fainted at all?",
    "And is there difficulty breathing at night", "Tell me if you have chest pain",
])
def test_a_question_with_a_lead_in_is_still_a_question(said):
    assert live_assist.red_flag_cards(live_assist.statements(said), set()) == []


# ---------------------------------------------------------------------------
# Kannada, Hindi and English (or a mix): live speech is translated to English first
# ---------------------------------------------------------------------------

def test_live_pieces_are_asked_for_in_translate_mode(monkeypatch):
    monkeypatch.setattr(live.transcribe, "transcribe_segments", fake_stt("I have had a fever"))
    live.ingest(live.open_session("c1", "a1", [], "tok"), speech_chunk(("talk", 1.5)))
    assert STT_CALLS == [{"mode": "translate"}]


def test_the_mode_can_be_switched_back_for_a_speech_service_without_translate(monkeypatch):
    monkeypatch.setattr(live, "LIVE_STT_MODE", "transcribe")
    monkeypatch.setattr(live.transcribe, "transcribe_segments", fake_stt("I have had a fever"))
    live.ingest(live.open_session("c1", "a1", [], "tok"), speech_chunk(("talk", 1.5)))
    assert STT_CALLS == [{"mode": "transcribe"}]


def test_the_post_visit_recording_is_still_transcribed_in_the_language_spoken():
    seen = []

    def post(url, files=None, timeout=None, data=None):
        seen.append(data)
        return type("R", (), {"status_code": 200, "json": lambda self: {"text": "ನಮಸ್ಕಾರ", "language_code": "kn-IN"}})()

    result = transcribe.transcribe_segment(audio.Segment(0, 1, 0, b"x"), "http://stt", post)
    assert result["text"] == "ನಮಸ್ಕಾರ" and seen == [None]           # no mode sent: the service's default is "transcribe"


@pytest.mark.parametrize("language", ["kn-IN", "hi-IN", "en-IN"])
def test_the_language_heard_is_remembered_as_a_short_code(language):
    s = live.open_session("c1", "a1", [], "tok")
    live.ingest(s, speech_chunk(("talk", 1.5)), fake_stt("I have had a fever since yesterday", language=language))
    assert live.snapshot(s)["languages"] == [language.split("-")[0]]


def test_the_language_follows_a_conversation_that_switches_language():
    s = live.open_session("c1", "a1", [], "tok")
    live.ingest(s, speech_chunk(("talk", 1.5), ("quiet", 0.1)), fake_stt("Any pain in the leg", language="en-IN"))
    assert live.snapshot(s)["languages"] == ["en"]
    live.ingest(s, speech_chunk(("quiet", 1.3), ("talk", 1.5)), fake_stt("Yes the leg hurts a lot and it is swollen", language="kn-IN"))
    assert live.snapshot(s)["languages"] == ["kn", "en"]                    # most spoken first
    assert [t["speaker"] for t in s.turns] == ["doctor", "patient"]        # one conversation, all English text
    assert s.words_since_run == 5 + 10


def test_noise_does_not_change_the_language():
    s = live.open_session("c1", "a1", [], "tok")
    live.ingest(s, speech_chunk(("talk", 1.0)), fake_stt("", language="kn-IN"))
    assert live.snapshot(s)["languages"] == []


@pytest.mark.parametrize("language,translated", [
    ("kn-IN", "I have chest pain since this morning?"),           # translated speech often gets a "?" on a statement
    ("hi-IN", "I have chest pain and difficulty breathing."),
])
def test_a_red_flag_spoken_in_kannada_or_hindi_fires_from_its_english_translation(language, translated):
    s = live.open_session("c1", "a1", [], "tok")
    live.ingest(s, speech_chunk(("talk", 1.5)), fake_stt(translated, language=language))
    labels = {c["text"] for c in live.immediate_red_flags(s)}
    assert any("Chest pain" in t or "chest pain" in t.lower() for t in labels)


@pytest.mark.parametrize("language,doctor,patient", [
    ("kn-IN", "Do you have any chest pain? Any difficulty breathing?", "No chest pain and no difficulty breathing."),
    ("hi-IN", "Is there difficulty in coughing or breathing?", "No cough? But I am very tired."),
])
def test_questions_and_denials_translated_from_kannada_or_hindi_do_not_raise_a_false_alarm(language, doctor, patient):
    s = live.open_session("c1", "a1", [], "tok")
    live.ingest(s, speech_chunk(("talk", 1.5), ("quiet", 1.4), ("talk", 1.5)), fake_stt(doctor, patient, language=language))
    assert live.immediate_red_flags(s) == []


def test_the_translate_mode_failure_is_the_same_quiet_failure(api, monkeypatch):
    def broken(segments, **kwargs):
        raise transcribe.SttError("HTTP 400: translate is not supported")

    monkeypatch.setattr(live.transcribe, "transcribe_segments", broken)
    assert post_chunk().json() == {"ok": False, "state": "error", "error": "speech_unavailable", "added_words": 0}


def test_the_page_is_told_which_language_was_heard_and_gets_english_lines_only(api, monkeypatch):
    monkeypatch.setattr(live, "run_engine", lambda *a, **k: [])
    monkeypatch.setattr(live.transcribe, "transcribe_segments",
                        fake_stt("Tell me about the pain", "It hurts when I walk", language="kn-IN"))
    assert client.get("/doctor/api/consultations/c1/live", headers=AUTH).json()["languages"] == []      # nothing heard yet
    post_chunk()
    body = client.get("/doctor/api/consultations/c1/live", headers=AUTH).json()
    assert body["languages"] == ["kn"]
    assert [t["text"] for t in body["turns"]] == ["Tell me about the pain", "It hurts when I walk"]


def test_a_short_piece_misdetected_as_another_language_does_not_change_what_is_shown():
    """Found live: a one-line piece of Kannada was heard as Hindi, and the last word as Tamil."""
    s = live.open_session("c1", "a1", [], "tok")
    live.ingest(s, speech_chunk(("talk", 1.5)), fake_stt("Hello please sit down what is the problem today", language="hi-IN"))
    long_kannada = "Hello Doctor I have had a cough and sore throat for three days and it increases at night " * 3
    live.ingest(s, speech_chunk(("quiet", 1.3), ("talk", 1.5)), fake_stt(long_kannada, language="kn-IN"))
    live.ingest(s, speech_chunk(("quiet", 1.3), ("talk", 1.5)), fake_stt("Okay Doctor", language="ta-IN"))
    assert live.snapshot(s)["languages"] == ["kn"]


def test_the_page_never_receives_words_in_another_language_only_codes_and_english_lines(api, monkeypatch):
    monkeypatch.setattr(live, "run_engine", lambda *a, **k: [])
    monkeypatch.setattr(live.transcribe, "transcribe_segments",
                        fake_stt("Tell me about the pain", "It hurts when I walk", language="hi-IN"))
    post_chunk()
    body = client.get("/doctor/api/consultations/c1/live", headers=AUTH).json()
    assert body["languages"] == ["hi"] and all(t["text"].isascii() for t in body["turns"])


# ---- red-flag wording that translation produces, found by running the real Kannada and Hindi audio ----

@pytest.mark.parametrize("said,label", [
    ("It becomes difficult to breathe while walking in the middle of the chest.", "Difficulty breathing"),
    ("I have been having difficulty in breathing since yesterday.", "Difficulty breathing"),
    ("She has breathing difficulties at night.", "Difficulty breathing"),
    ("I am unable to breathe properly.", "Difficulty breathing"),
    ("There is a pain in my heart since morning.", "Chest pain"),
    ("My chest is paining a lot.", "Chest pain"),
    ("He lost consciousness for a minute.", "Fainting or loss of consciousness"),
    ("There was a lot of blood from the cut.", "Heavy bleeding"),
    ("The bleeding is not stopping.", "Heavy bleeding"),
    ("His speech is slurred since morning.", "Possible stroke signs"),
])
def test_wording_that_translated_speech_uses_still_fires_a_red_flag(said, label):
    cards = live_assist.red_flag_cards(live_assist.statements(said), set())
    assert label in [c["text"].split(" was mentioned")[0] for c in cards], said


@pytest.mark.parametrize("said", [
    "No, it is not difficult to breathe.",
    "There is no pain in the chest and no difficulty in breathing either.",
    "Is it difficult to breathe at night?",
    "Do you have heart pain or a lot of blood loss?",
    "I did not lose consciousness.",
    "The cut is small and there was no bleeding a lot.",
])
def test_denials_and_questions_in_the_new_wording_do_not_fire(said):
    assert live_assist.red_flag_cards(live_assist.statements(said), set()) == [], said


# ---- found by running the mixed Kannada/English audio: a false model "red flag" and a card from a mis-heard word ----

def test_urgent_wording_alone_does_not_make_a_model_red_flag():
    transcript = "Yes doctor, there is a lot of stress at work. I only sleep for four hours."
    cards = [{"kind": "red_flag", "text": "The patient reports sleeping only four hours, which may need urgent attention.",
              "evidence": "I only sleep for four hours."}]
    kept, _ = live_assist.check_cards(cards, transcript, "", [])
    assert [c["kind"] for c in kept] == ["consider"]


def test_a_model_red_flag_with_a_real_alarm_word_stays_a_red_flag():
    transcript = "My foot feels numb and looks pale since the fall."
    cards = [{"kind": "red_flag", "text": "A numb, pale foot may need urgent attention.", "evidence": "My foot feels numb and looks pale"}]
    kept, _ = live_assist.check_cards(cards, transcript, "", [])
    assert [c["kind"] for c in kept] == ["red_flag"]


@pytest.mark.parametrize("evidence", ["Sorry, Doctor.", "okay doctor", "Hello", "Yes, thank you doctor"])
def test_a_card_quoting_only_greetings_or_chatter_is_dropped(evidence):
    transcript = "Hello. Sorry, Doctor. Okay doctor. Yes, thank you doctor."
    cards = [{"kind": "consider", "text": "Keep in mind the patient apologised during the consultation.", "evidence": evidence}]
    kept, dropped = live_assist.check_cards(cards, transcript, "", [])
    assert kept == [] and dropped == {"weak_evidence": 1}
