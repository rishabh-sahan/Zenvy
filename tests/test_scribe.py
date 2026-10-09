"""
The scribe: audio splitting, doctor/patient labelling, retries, the English
note and the pipeline's handling of failures. No network: speech-to-text, the
language model and Team C are replaced by fakes.
"""
import io
import json
import math
import shutil
import struct
import sys
import types
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import requests

from services.scribe import audio, pipeline, summarize, transcribe
from services.scribe.audio import AudioError, split_on_speech
from services.scribe.team_c import TeamCError

RATE = 16000


# ---------------------------------------------------------------------------
# test audio: loud tone = speech, near-silence = pause
# ---------------------------------------------------------------------------

def tone(seconds, amplitude=6000, freq=440):
    n = int(RATE * seconds)
    return [int(amplitude * math.sin(2 * math.pi * freq * i / RATE)) for i in range(n)]


def quiet(seconds, amplitude=12):
    n = int(RATE * seconds)
    return [int(amplitude * math.sin(2 * math.pi * 97 * i / RATE)) for i in range(n)]


def wav_of(*parts):
    samples = [s for part in parts for s in part]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


def seconds_of(wav_bytes):
    return audio.wav_seconds(wav_bytes)


# ---------------------------------------------------------------------------
# audio.split_on_speech
# ---------------------------------------------------------------------------

def test_speech_is_cut_at_the_pauses():
    # speech 1.5 | pause 1.2 | speech 2.0 + 0.3 pause + 1.0 | pause 2.0 | speech 1.0
    recording = wav_of(quiet(0.5), tone(1.5), quiet(1.2), tone(2.0), quiet(0.3), tone(1.0), quiet(2.0), tone(1.0), quiet(0.5))
    segments = split_on_speech(recording)

    assert len(segments) == 3
    assert segments[0].start == pytest.approx(0.5, abs=0.1)
    assert segments[0].end == pytest.approx(2.0, abs=0.1)
    # The 0.3 s pause is too short to split on, so these stay together.
    assert segments[1].start == pytest.approx(3.2, abs=0.1)
    assert segments[1].end - segments[1].start == pytest.approx(3.3, abs=0.15)
    assert segments[2].start == pytest.approx(8.5, abs=0.1)

    assert segments[0].gap_before == pytest.approx(0.5, abs=0.1)
    assert segments[1].gap_before == pytest.approx(1.2, abs=0.1)
    assert segments[2].gap_before == pytest.approx(2.0, abs=0.1)


def test_each_segment_is_a_valid_standalone_wav_with_a_little_padding():
    segments = split_on_speech(wav_of(quiet(1.0), tone(2.0), quiet(1.0)))
    assert len(segments) == 1
    length = seconds_of(segments[0].wav)
    assert 2.0 < length < 2.5  # the speech plus padding, not the whole recording


def test_silence_has_no_speech():
    assert split_on_speech(wav_of(quiet(5.0, amplitude=0))) == []
    assert split_on_speech(wav_of(quiet(5.0, amplitude=40))) == []


def test_a_tiny_click_is_not_speech():
    assert split_on_speech(wav_of(quiet(1.0), tone(0.05), quiet(1.0))) == []


def test_a_continuous_monologue_is_cut_into_short_clips():
    segments = split_on_speech(wav_of(tone(70.0)))
    assert len(segments) >= 3
    for segment in segments:
        assert segment.end - segment.start <= audio.MAX_SEGMENT_SECONDS + 0.05
    # No speech is lost between the cuts.
    covered = sum(s.end - s.start for s in segments)
    assert covered == pytest.approx(70.0, abs=0.3)


def test_a_very_short_consultation_still_works():
    segments = split_on_speech(wav_of(quiet(0.2), tone(3.0), quiet(0.2)))
    assert len(segments) == 1


def test_unreadable_audio_is_an_error_not_a_crash():
    with pytest.raises(AudioError):
        split_on_speech(b"not a wav file at all")
    with pytest.raises(AudioError):
        split_on_speech(b"")


def test_a_recording_longer_than_an_hour_is_refused(monkeypatch):
    monkeypatch.setattr(audio, "MAX_RECORDING_SECONDS", 2)
    with pytest.raises(AudioError, match="one hour"):
        split_on_speech(wav_of(tone(3.0)))


def test_stereo_audio_is_refused_with_a_clear_error():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(b"\x00\x01" * 2000)
    with pytest.raises(AudioError):
        split_on_speech(buffer.getvalue())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed on this machine")
def test_any_recording_is_converted_to_16k_mono_wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:  # 44.1 kHz stereo
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(struct.pack("<2h", 3000, -3000) * 44100)
    converted = audio.to_wav_16k_mono(buffer.getvalue(), ".wav")
    with wave.open(io.BytesIO(converted), "rb") as handle:
        assert (handle.getnchannels(), handle.getframerate(), handle.getsampwidth()) == (1, 16000, 2)
    with pytest.raises(AudioError):
        audio.to_wav_16k_mono(b"\x00\x01garbage", ".webm")
    with pytest.raises(AudioError):
        audio.to_wav_16k_mono(b"", ".webm")


# ---------------------------------------------------------------------------
# transcribe: labelling
# ---------------------------------------------------------------------------

def seg(start, end, gap=0.0):
    return audio.Segment(start=start, end=end, gap_before=gap, wav=b"")


def heard(text, language="en-IN"):
    return {"text": text, "language_code": language}


def test_the_doctor_speaks_first_and_a_long_pause_changes_the_speaker():
    segments = [seg(0, 2), seg(3.2, 6), seg(6.7, 8), seg(9.4, 11)]
    results = [heard("What brings you in?"), heard("I have a fever."), heard("Since three days."), heard("Any cough?")]
    turns = transcribe.label_turns(segments, results)

    assert [t["speaker"] for t in turns] == ["doctor", "patient", "doctor"]
    # A 0.7 s pause is too short to be a new turn: same speaker, joined.
    assert turns[1]["text"] == "I have a fever. Since three days."
    assert turns[1]["start_seconds"] == 3.2 and turns[1]["end_seconds"] == 8
    assert turns[0]["language_code"] == "en-IN"


def test_noise_segments_are_dropped_and_do_not_confuse_the_pauses():
    segments = [seg(0, 2), seg(2.5, 3.0), seg(4.5, 6)]
    results = [heard("Hello."), heard(""), heard("Hi doctor.")]
    turns = transcribe.label_turns(segments, results)
    assert [(t["speaker"], t["text"]) for t in turns] == [("doctor", "Hello."), ("patient", "Hi doctor.")]


def test_nothing_heard_gives_no_turns():
    assert transcribe.label_turns([seg(0, 1)], [heard("")]) == []
    assert transcribe.label_turns([], []) == []


def test_the_dominant_language_is_the_one_spoken_longest():
    turns = [
        {"language_code": "hi-IN", "start_seconds": 0, "end_seconds": 10},
        {"language_code": "en-IN", "start_seconds": 10, "end_seconds": 12},
        {"language_code": "hi-IN", "start_seconds": 12, "end_seconds": 15},
    ]
    assert transcribe.dominant_language(turns) == "hi-IN"
    assert transcribe.dominant_language([]) is None


# ---------------------------------------------------------------------------
# transcribe: calling the speech-to-text service
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    monkeypatch.setattr(transcribe.time, "sleep", lambda s: None)
    monkeypatch.setattr(summarize.time, "sleep", lambda s: None)


def test_a_failed_attempt_is_retried():
    calls = []

    def post(url, files=None, timeout=None):
        calls.append(1)
        return FakeResponse(502) if len(calls) < 3 else FakeResponse(200, {"text": " Hello ", "language_code": "en-IN"})

    assert transcribe.transcribe_segment(seg(0, 1), "http://stt", post) == {"text": "Hello", "language_code": "en-IN"}
    assert len(calls) == 3


def test_it_gives_up_after_three_attempts():
    calls = []

    def post(url, files=None, timeout=None):
        calls.append(1)
        raise requests.exceptions.ConnectionError("down")

    with pytest.raises(transcribe.SttError):
        transcribe.transcribe_segment(seg(0, 1), "http://stt", post)
    assert len(calls) == 3


def test_a_rejected_request_is_not_retried():
    calls = []

    def post(url, files=None, timeout=None):
        calls.append(1)
        return FakeResponse(400)

    with pytest.raises(transcribe.SttError):
        transcribe.transcribe_segment(seg(0, 1), "http://stt", post)
    assert len(calls) == 1


def test_segments_come_back_in_order_even_when_done_in_parallel():
    segments = [audio.Segment(i, i + 1, 0, str(i).encode()) for i in range(12)]

    def post(url, files=None, timeout=None):
        return FakeResponse(200, {"text": "t" + files["file"][1].decode(), "language_code": "en-IN"})

    results = transcribe.transcribe_segments(segments, "http://stt", post)
    assert [r["text"] for r in results] == [f"t{i}" for i in range(12)]


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

GOOD = (
    '{"chief_complaint": "Fever for three days", "discussion_points": ["Temperature 101F", "No cough"], '
    '"assessment": "Viral fever", "plan": "Paracetamol 500 mg twice daily; review in one week"}'
)

TURNS = [
    {"speaker": "doctor", "text": "What brings you in?"},
    {"speaker": "patient", "text": "I have had a fever for three days."},
    {"speaker": "doctor", "text": "Take paracetamol and come back in a week."},
]


def test_a_note_is_read_from_a_plain_json_reply():
    note = summarize.parse_note(GOOD)
    assert note["chief_complaint"] == "Fever for three days"
    assert note["discussion_points"] == ["Temperature 101F", "No cough"]
    assert note["plan"].startswith("Paracetamol")


@pytest.mark.parametrize("wrapped", [
    "```json\n" + GOOD + "\n```",
    "<think>let me think about this</think>\n" + GOOD,
    "Here is the note:\n" + GOOD + "\nHope this helps!",
])
def test_a_note_is_found_inside_fences_thinking_and_chatter(wrapped):
    assert summarize.parse_note(wrapped)["assessment"] == "Viral fever"


def test_discussion_points_given_as_text_become_a_list():
    note = summarize.parse_note('{"chief_complaint": "x", "discussion_points": "- one\\n- two", "assessment": "", "plan": ""}')
    assert note["discussion_points"] == ["one", "two"]


@pytest.mark.parametrize("bad", ["", "no json here", "{broken", "[1, 2]", '{"chief_complaint": "", "plan": ""}'])
def test_an_unusable_reply_is_an_error(bad):
    with pytest.raises(summarize.SummaryError):
        summarize.parse_note(bad)


def test_the_prompt_carries_the_labelled_transcript_and_the_safety_rules():
    seen = []

    def chat(messages):
        seen.append(messages)
        return GOOD

    note = summarize.summarize(TURNS, chat=chat)
    assert note["plan"]
    system, user = seen[0][0]["content"], seen[0][1]["content"]
    assert "Doctor: What brings you in?" in user and "Patient: I have had a fever" in user
    assert "ENGLISH" in system and "Never invent" in system and "ONLY" in system


def test_a_note_that_is_not_in_english_is_asked_for_again():
    hindi = '{"chief_complaint": "तीन दिन से बुखार", "discussion_points": ["तापमान"], "assessment": "वायरल बुखार", "plan": "पैरासिटामोल"}'
    replies = iter([hindi, GOOD])
    systems = []

    def chat(messages):
        systems.append(messages[0]["content"])
        return next(replies)

    note = summarize.summarize(TURNS, chat=chat)
    assert note["assessment"] == "Viral fever"
    assert len(systems) == 2 and "not in English" in systems[1] and "not in English" not in systems[0]


def test_if_the_model_keeps_answering_in_hindi_the_note_is_translated():
    hindi = '{"chief_complaint": "तीन दिन से बुखार", "discussion_points": [], "assessment": "वायरल बुखार", "plan": "पैरासिटामोल"}'
    note = summarize.summarize(TURNS, chat=lambda m: hindi, translate=lambda text: "translated:" + str(len(text)))
    assert summarize.is_english(note)
    assert note["assessment"].startswith("translated:")


def test_a_note_that_cannot_be_made_english_is_an_error_not_a_hindi_note():
    hindi = '{"chief_complaint": "तीन दिन से बुखार", "discussion_points": [], "assessment": "x", "plan": "y"}'
    with pytest.raises(summarize.SummaryError) as caught:
        summarize.summarize(TURNS, chat=lambda m: hindi, translate=lambda text: text)
    assert caught.value.code == "summary_not_english"


def test_an_empty_transcript_is_refused_without_calling_the_model():
    def chat(messages):
        raise AssertionError("the model must not be called")

    for turns in ([], [{"speaker": "doctor", "text": "   "}]):
        with pytest.raises(summarize.SummaryError) as caught:
            summarize.summarize(turns, chat=chat)
        assert caught.value.code == "empty_transcript"


def test_a_long_consultation_is_summarised_in_parts_and_merged():
    long_turns = [{"speaker": "doctor", "text": "word " * 800} for _ in range(6)]  # ~24k characters
    calls = []

    def chat(messages):
        calls.append(messages[0]["content"])
        return GOOD

    note = summarize.summarize(long_turns, chat=chat)
    assert note["assessment"] == "Viral fever"
    assert len(calls) >= 3  # several parts, then one merge
    assert calls[-1] == summarize.MERGE_PROMPT


def test_if_the_merge_fails_nothing_from_the_parts_is_lost():
    long_turns = [{"speaker": "doctor", "text": "word " * 800} for _ in range(6)]
    parts = iter([GOOD] * 20)

    def chat(messages):
        if messages[0]["content"] == summarize.MERGE_PROMPT:
            return "sorry, I cannot"
        return next(parts)

    note = summarize.summarize(long_turns, chat=chat)
    assert note["chief_complaint"] == "Fever for three days"
    assert note["discussion_points"].count("No cough") >= 2


def test_the_model_is_retried_on_a_server_error_but_not_on_a_bad_request(monkeypatch):
    fake_config = types.SimpleNamespace(SARVAM_API_KEY="k", SARVAM_BASE_URL="http://sarvam")
    monkeypatch.setitem(sys.modules, "services.config", fake_config)
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            return FakeResponse(503)
        return FakeResponse(200, {"choices": [{"message": {"content": GOOD}}]})

    monkeypatch.setattr(requests, "post", post)
    assert summarize.call_chat([{"role": "user", "content": "x"}]) == GOOD
    assert len(calls) == 3

    calls.clear()
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or FakeResponse(400))
    with pytest.raises(summarize.SummaryError):
        summarize.call_chat([{"role": "user", "content": "x"}])
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------

class FakeTeamC:
    def __init__(self):
        self.statuses, self.transcript, self.notes = [], None, []
        self.fail_on = None
        self.turns_to_return = []

    def set_status(self, cid, status, reason=None):
        if self.fail_on == "status":
            raise TeamCError(503, "down")
        self.statuses.append((status, reason))

    def put_transcript(self, cid, turns, language):
        if self.fail_on == "transcript":
            raise RuntimeError("boom")
        self.transcript = (turns, language)
        self.turns_to_return = turns

    def add_note(self, cid, note, source):
        self.notes.append((note, source))
        return {"version": len(self.notes)}

    def get_consultation(self, cid):
        return {"turns": self.turns_to_return}


# What the fake speech-to-text "hears", chosen by how long the clip is.
SPOKEN = {1.8: "What brings you in today?", 2.3: "I have had a fever for three days.", 1.3: "Take paracetamol and return in a week."}


def fake_stt(url, files=None, timeout=None):
    wav = files["file"][1]
    length = round(audio.wav_seconds(wav), 1)
    text = SPOKEN[min(SPOKEN, key=lambda k: abs(k - length))]
    return FakeResponse(200, {"text": text, "language_code": "en-IN"})


CONSULT = wav_of(quiet(0.5), tone(1.5), quiet(1.4), tone(2.0), quiet(1.4), tone(1.0), quiet(0.5))


@pytest.fixture
def team_c():
    return FakeTeamC()


def run(team_c, wav=CONSULT, **overrides):
    kwargs = dict(team_c=team_c, stt_url="http://stt", post=fake_stt, chat=lambda m: GOOD)
    kwargs.update(overrides)
    return pipeline.process_recording("c1", wav, "token", **kwargs)


def test_a_recording_becomes_a_transcript_and_a_draft_note(team_c):
    assert run(team_c) == "draft_ready"

    assert [s for s, _ in team_c.statuses] == ["transcribing", "summarising"]
    turns, language = team_c.transcript
    assert [(t["speaker"], t["text"]) for t in turns] == [
        ("doctor", "What brings you in today?"),
        ("patient", "I have had a fever for three days."),
        ("doctor", "Take paracetamol and return in a week."),
    ]
    assert language == "en-IN"
    (note, source), = team_c.notes
    assert source == "ai" and note["assessment"] == "Viral fever"


def test_a_silent_recording_fails_cleanly_with_no_speech(team_c):
    assert run(team_c, wav_of(quiet(5.0, amplitude=0))) == "failed:no_speech"
    assert team_c.statuses[-1] == ("failed", "no_speech")
    assert team_c.notes == [] and team_c.transcript is None


def test_a_corrupt_recording_fails_cleanly(team_c):
    assert run(team_c, b"RIFFgarbage") == "failed:audio_unreadable"
    assert team_c.statuses[-1] == ("failed", "audio_unreadable")


def test_an_unrecognisable_recording_fails_with_no_speech(team_c):
    assert run(team_c, post=lambda *a, **k: FakeResponse(200, {"text": "", "language_code": "en-IN"})) == "failed:no_speech"


def test_a_speech_to_text_outage_is_reported(team_c):
    assert run(team_c, post=lambda *a, **k: FakeResponse(502)) == "failed:speech_to_text_failed"
    assert team_c.statuses[-1] == ("failed", "speech_to_text_failed")


def test_a_language_model_failure_keeps_the_transcript(team_c):
    def chat(messages):
        raise summarize.SummaryError("summary_failed", "model down")

    assert run(team_c, chat=chat) == "failed:summary_failed"
    assert team_c.transcript is not None  # the doctor still has the transcript
    assert team_c.statuses[-1] == ("failed", "summary_failed")


def test_an_unexpected_error_never_escapes_the_background_task(team_c):
    team_c.fail_on = "transcript"
    assert run(team_c) == "failed:unexpected_error"


def test_a_team_c_status_problem_does_not_stop_the_work(team_c):
    team_c.fail_on = "status"
    assert run(team_c) == "draft_ready"
    assert len(team_c.notes) == 1


def test_a_very_short_consultation_gets_a_note(team_c):
    short = wav_of(quiet(0.2), tone(1.5), quiet(0.2))
    assert run(team_c, short, post=lambda *a, **k: FakeResponse(200, {"text": "My head hurts.", "language_code": "en-IN"})) == "draft_ready"


def test_the_same_consultation_is_not_processed_twice_at_once(team_c):
    pipeline._running.add("c1")
    try:
        assert run(team_c) == "already_running"
        assert team_c.statuses == []
    finally:
        pipeline._running.discard("c1")
    # ...and it is free to run again afterwards.
    assert run(team_c) == "draft_ready"
    assert "c1" not in pipeline._running


def test_the_slot_is_released_even_after_a_failure(team_c):
    run(team_c, wav_of(quiet(3.0, amplitude=0)))
    assert "c1" not in pipeline._running


def test_regenerating_makes_a_new_version_from_the_current_transcript(team_c):
    team_c.turns_to_return = TURNS
    saved = pipeline.regenerate_note("c1", "token", team_c=team_c, chat=lambda m: GOOD)
    assert saved == {"version": 1}
    assert team_c.notes[0][1] == "ai_regenerated"


def test_regenerating_without_a_transcript_is_refused(team_c):
    with pytest.raises(pipeline.PipelineFailure) as caught:
        pipeline.regenerate_note("c1", "token", team_c=team_c, chat=lambda m: GOOD)
    assert caught.value.code == "no_transcript"


def test_a_failed_regeneration_reports_why(team_c):
    team_c.turns_to_return = TURNS

    def chat(messages):
        raise summarize.SummaryError("summary_failed", "model down")

    with pytest.raises(pipeline.PipelineFailure) as caught:
        pipeline.regenerate_note("c1", "token", team_c=team_c, chat=chat)
    assert caught.value.code == "summary_failed"
    assert team_c.notes == []


# ---------------------------------------------------------------------------
# the medicine draft that follows the note
# ---------------------------------------------------------------------------

class PrescribingTeamC(FakeTeamC):
    def __init__(self):
        super().__init__()
        self.prescriptions = []

    def put_prescription(self, cid, items, source):
        self.prescriptions.append((items, source))
        return {"status": "draft"}


def chat_for_both(medicines):
    def chat(messages):
        if "clinical scribe" in messages[0]["content"]:
            return GOOD
        return json.dumps({"medicines": medicines})
    return chat


def test_a_medicine_draft_is_saved_after_the_note(team_c=None):
    team_c = PrescribingTeamC()
    run(team_c, chat=chat_for_both([{"drug_name": "paracetamol", "frequency_text": "twice a day"}]))
    (items, source), = team_c.prescriptions
    assert source == "transcript" and items[0]["drug_name"] == "paracetamol" and items[0]["from_transcript"] is True
    assert [s for s, _ in team_c.statuses] == ["transcribing", "summarising"]      # status flow unchanged


def test_no_medicine_in_the_recording_saves_no_draft():
    team_c = PrescribingTeamC()
    assert run(team_c, chat=chat_for_both([])) == "draft_ready"
    assert team_c.prescriptions == []


def test_a_medicine_the_recording_never_mentioned_is_not_drafted():
    team_c = PrescribingTeamC()
    run(team_c, chat=chat_for_both([{"drug_name": "Amoxicillin"}]))
    assert team_c.prescriptions == []


def test_a_failing_medicine_step_never_fails_the_consultation():
    team_c = PrescribingTeamC()

    def chat(messages):
        if "clinical scribe" in messages[0]["content"]:
            return GOOD
        raise summarize.SummaryError("summary_failed", "model down")

    assert run(team_c, chat=chat) == "draft_ready"
    assert len(team_c.notes) == 1 and team_c.prescriptions == []                 # the note is safe


def test_a_team_c_error_while_saving_the_draft_never_fails_the_consultation():
    class Refusing(PrescribingTeamC):
        def put_prescription(self, cid, items, source):
            raise TeamCError(500, "database down")

    team_c = Refusing()
    assert run(team_c, chat=chat_for_both([{"drug_name": "paracetamol"}])) == "draft_ready"
    assert len(team_c.notes) == 1
