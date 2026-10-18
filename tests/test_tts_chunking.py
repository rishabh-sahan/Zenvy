"""A reply longer than the speech service accepts (500 characters) is spoken in pieces and joined.

Found by the patient page: the three-doctor suggestion is about 590 characters, so every SPOKEN reply with doctors
failed with "Text exceeds 500 character limit" and the page said "could not connect to voice service".
"""
import io
import os
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
_dummy_key = not os.environ.get("SARVAM_API_KEY")
if _dummy_key:
    os.environ["SARVAM_API_KEY"] = "dummy-key-for-import"

from services.gateway import main as gateway
from services.orchestrator import state_machine

if _dummy_key:
    del os.environ["SARVAM_API_KEY"]

SPEECH_LIMIT = 500

# the real reply that failed on the patient page (608 characters): five doctors from both cities
THREE_DOCTORS = (
    "Which of these doctors would you like to book? "
    "1) Dr. Kavya Hegde, Bengaluru Multispeciality Centre, Bengaluru (next free Mon 19 Oct at 09:00); "
    "2) Dr. Kavya Hegde, Mysuru Multispeciality Hospital, Mysuru (next free Mon 19 Oct at 09:00); "
    "3) Dr. Nandini Iyer, Bengaluru Multispeciality Centre, Bengaluru (next free Mon 19 Oct at 09:30); "
    "4) Dr. Nandini Iyer, Mysuru Multispeciality Hospital, Mysuru (next free Mon 19 Oct at 09:30); "
    "5) Dr. Priya Sharma, Bengaluru Multispeciality Centre, Bengaluru (next free Mon 19 Oct at 10:00). "
    "You can say the number, or say no thanks."
)


def wav(seconds: float, rate: int = 16000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return out.getvalue()


class Piece:
    def __init__(self, blob):
        self.content = blob
        self.status_code = 200
        self.ok = True


@pytest.fixture
def speech(monkeypatch):
    """A speech service that refuses more than 500 characters, like the real one, and says each piece in 1 second."""
    calls = []

    def one(text, short_lang):
        assert len(text) <= SPEECH_LIMIT, f"the speech service would refuse {len(text)} characters"
        calls.append(text)
        return Piece(wav(1.0))

    monkeypatch.setattr(gateway, "_tts_one", one)
    return calls


def seconds_of(blob: bytes) -> float:
    with wave.open(io.BytesIO(blob)) as w:
        return w.getnframes() / w.getframerate()


def test_the_real_three_doctor_reply_was_too_long_for_one_call():
    assert len(THREE_DOCTORS) > SPEECH_LIMIT


def test_the_three_doctor_reply_is_spoken_in_pieces_and_joined(speech):
    reply = gateway._call_tts(THREE_DOCTORS, "en")
    assert len(speech) >= 2 and all(len(p) <= SPEECH_LIMIT for p in speech)
    assert seconds_of(reply.content) == pytest.approx(len(speech))                  # every piece is in the audio, in one file
    assert reply.status_code == 200 and reply.ok


def test_nothing_is_lost_or_reordered_when_the_text_is_cut(speech):
    gateway._call_tts(THREE_DOCTORS, "en")
    assert " ".join(speech) == THREE_DOCTORS


def test_the_cut_is_between_sentences_or_list_items_not_in_a_name(speech):
    gateway._call_tts(THREE_DOCTORS, "en")
    for piece in speech[:-1]:
        assert piece.endswith((".", ";", "?", "!"))


def test_a_short_reply_is_one_call_and_unchanged(speech):
    reply = gateway._call_tts("  Your appointment is confirmed.  ", "en")
    assert speech == ["Your appointment is confirmed."] and seconds_of(reply.content) == pytest.approx(1.0)


def test_a_reply_of_exactly_the_limit_is_one_call(speech):
    gateway._call_tts("a" * 450, "en")
    assert len(speech) == 1


def test_hindi_sentences_end_with_a_danda_and_are_cut_there(speech):
    sentence = "आपके लिए मैं न्यूरोलॉजिस्ट से मिलने की सलाह देता हूँ। " * 12
    gateway._call_tts(sentence, "hi")
    assert len(speech) >= 2 and all(p.endswith("।") for p in speech)


def test_a_long_text_with_no_punctuation_is_cut_at_spaces(speech):
    gateway._call_tts("word " * 400, "en")
    assert len(speech) >= 4 and all(len(p) <= SPEECH_LIMIT for p in speech) and all(p.split() for p in speech)
    assert sum(len(p.split()) for p in speech) == 400


def test_text_with_no_spaces_at_all_is_still_cut(speech):
    gateway._call_tts("x" * 1200, "en")
    assert [len(p) for p in speech] == [450, 450, 300]


def test_one_enormous_sentence_among_short_ones(speech):
    gateway._call_tts("Short one. " + "long " * 300 + "end. Last one.", "en")
    assert all(len(p) <= SPEECH_LIMIT for p in speech) and speech[0].startswith("Short one.") and speech[-1].endswith("Last one.")


def test_pieces_in_different_audio_formats_are_refused_cleanly(monkeypatch):
    formats = iter([wav(1.0, 16000), wav(1.0, 8000)])
    monkeypatch.setattr(gateway, "_tts_one", lambda text, lang: Piece(next(formats)))
    with pytest.raises(gateway.HTTPException) as refused:
        gateway._call_tts(THREE_DOCTORS, "en")
    assert refused.value.status_code == 502


def test_a_failing_piece_fails_the_whole_reply_loudly(monkeypatch):
    def broken(text, lang):
        raise gateway.HTTPException(status_code=502, detail="TTS service failed")

    monkeypatch.setattr(gateway, "_tts_one", broken)
    with pytest.raises(gateway.HTTPException):
        gateway._call_tts(THREE_DOCTORS, "en")


def test_an_empty_reply_still_raises_like_before(monkeypatch):
    monkeypatch.setattr(gateway, "_tts_one", lambda text, lang: (_ for _ in ()).throw(gateway.HTTPException(status_code=500, detail="empty")))
    with pytest.raises(gateway.HTTPException):
        gateway._call_tts("   ", "en")


def test_every_real_suggestion_the_agent_can_write_fits_after_splitting(speech):
    """The widest case: three doctors, the longest hospital names, in the three languages."""
    for lang in ("en", "hi", "kn"):
        options = "; ".join(
            state_machine._suggestion_option(i + 1, {
                "name": "Dr. Nandini Iyer", "hospital_name": "Silicon City Specialty Hospital", "city": "bangalore",
                "next_slot": "2030-03-04T09:30:00+05:30"}, lang) for i in range(3))
        reply = state_machine.render_template("DOCTOR_SUGGEST_OTHER_CITY", lang, specialty="Ophthalmologist", options=options)
        speech.clear()
        gateway._call_tts(reply, lang)
        assert speech and all(len(p) <= SPEECH_LIMIT for p in speech), lang
