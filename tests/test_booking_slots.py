"""
Booking conversation with slot locking (orchestrator side).

Team C is replaced by a small in-memory fake that behaves like the real
service: a slot can be held by one session at a time, booking needs the hold,
and a second patient gets "slot unavailable". Redis is a dict. The NLU is
scripted, so no network, database or API key is needed.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest
import requests
from dotenv import load_dotenv

# services.config refuses to import without SARVAM_API_KEY. These tests never
# call Sarvam, so supply a dummy key just for the import and then take it away
# again -- leaving it set would break tests that use the real key.
load_dotenv(ROOT / ".env")
_dummy_key = not os.environ.get("SARVAM_API_KEY")
if _dummy_key:
    os.environ["SARVAM_API_KEY"] = "dummy-key-for-import"

from services import conversation_client
from services.conversation_client import SlotUnavailableError
from services.orchestrator import state_machine

if _dummy_key:
    del os.environ["SARVAM_API_KEY"]

IST = timezone(timedelta(hours=5, minutes=30))
DAY = "2030-03-04"


class FakeRedis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, ttl, value):
        self.data[key] = value

    def delete(self, key):
        self.data.pop(key, None)


def _doctor(doctor_id, name, specialty="Cardiologist", hospital="Zenvy Care Hospital", city="mysore"):
    return {
        "doctor_id": doctor_id,
        "name": name,
        "specialty": specialty,
        "hospital_name": hospital,
        "city": city,
        "slot_minutes": 30,
    }


class FakeTeamC:
    """Just enough of Team C: doctors, free slots, holds, bookings."""

    def __init__(self):
        self.doctors = [_doctor("d1", "Dr. Arjun Rao")]
        self.free_times = ["09:30", "10:00", "10:30", "11:00", "11:30"]
        self.held = {}  # slot_id -> session_id
        self.booked = set()
        self.appointments = []
        self.down = False
        self.notification_fails = False

    def slot_id(self, doctor_id, day, hhmm):
        return f"{doctor_id}|{day}|{hhmm}"

    def _slot(self, doctor_id, day, hhmm):
        start = datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=IST)
        return {"slot_id": self.slot_id(doctor_id, day, hhmm), "slot_start": start.isoformat()}

    # --- the functions the state machine imports ---------------------------
    def find_doctors(self, query):
        if self.down:
            raise requests.exceptions.ConnectionError("team c down")
        q = query.lower().replace("dr.", "").strip()
        return [d for d in self.doctors if q in d["name"].lower() or q in d["specialty"].lower()]

    def get_free_slots(self, doctor_id, day, near=None, limit=3):
        if day != DAY:
            return []
        free = [
            t for t in self.free_times
            if self.slot_id(doctor_id, day, t) not in self.booked
            and self.slot_id(doctor_id, day, t) not in self.held
        ]
        if near:
            minutes = lambda t: int(t[:2]) * 60 + int(t[3:5])
            free = sorted(free, key=lambda t: abs(minutes(t) - minutes(near)))[:limit]
            free = sorted(free)
        return [self._slot(doctor_id, day, t) for t in free]

    def hold_slot(self, slot_id, session_id):
        if slot_id in self.booked or self.held.get(slot_id, session_id) != session_id:
            raise SlotUnavailableError(slot_id)
        self.held[slot_id] = session_id
        return {"slot_id": slot_id}

    def release_slot(self, slot_id, session_id):
        if self.held.get(slot_id) == session_id:
            del self.held[slot_id]
            return True
        return False

    def create_appointment(self, session_id, patient_uhid, slot_id=None, **kwargs):
        if self.held.get(slot_id) != session_id:
            raise SlotUnavailableError(slot_id)
        del self.held[slot_id]
        self.booked.add(slot_id)
        self.appointments.append({"session_id": session_id, "slot_id": slot_id, **kwargs})
        if self.notification_fails:
            return {"notification_failed": True}
        return {"appointment_id": f"apt-{len(self.appointments)}"}


@pytest.fixture
def team_c(monkeypatch):
    fake = FakeTeamC()
    monkeypatch.setattr(state_machine, "redis_client", FakeRedis())
    for name in ("find_doctors", "get_free_slots", "hold_slot", "release_slot", "create_appointment"):
        monkeypatch.setattr(state_machine, name, getattr(fake, name))
    return fake


@pytest.fixture
def say(monkeypatch):
    """Run one patient turn with scripted NLU output."""

    def _say(session_id, text, lang="en", **fields):
        extracted = {
            "wants_to_book": False,
            "doctor_name": None,
            "appointment_date": None,
            "appointment_time": None,
            "confirms_booking": None,
        }
        extracted.update(fields)
        monkeypatch.setattr(state_machine, "extract_booking_fields", lambda _text: extracted)
        return state_machine.handle_turn(session_id, lang, text)

    return _say


def _state(session_id):
    return state_machine._get_session_state(session_id)


def test_everything_in_one_message_holds_the_slot_then_yes_books_it(team_c, say):
    reply = say("s1", "book Dr Arjun tomorrow 10:30", wants_to_book=True,
                doctor_name="Arjun Rao", appointment_date=DAY, appointment_time="10:30")

    assert "Dr. Arjun Rao" in reply and DAY in reply and "10:30" in reply
    slot = team_c.slot_id("d1", DAY, "10:30")
    assert team_c.held == {slot: "s1"}
    assert _state("s1")["state"] == "CONFIRM"

    reply = say("s1", "yes", confirms_booking=True)

    assert "confirmed" in reply.lower()
    assert team_c.booked == {slot}
    assert team_c.held == {}
    # The booking is made against the SLOT; doctor and time are not sent.
    assert team_c.appointments == [{"session_id": "s1", "slot_id": slot, "status": "confirmed"}]
    assert _state("s1") is None


def test_collecting_details_one_at_a_time(team_c, say):
    assert "doctor" in say("s1", "book", wants_to_book=True).lower()
    assert "date" in say("s1", "Arjun Rao", doctor_name="Arjun Rao").lower()
    assert "time" in say("s1", "tomorrow", appointment_date=DAY).lower()
    reply = say("s1", "ten thirty", appointment_time="10:30")
    assert "Dr. Arjun Rao" in reply
    assert team_c.held == {team_c.slot_id("d1", DAY, "10:30"): "s1"}


def test_a_time_written_as_9_30_still_matches_the_09_30_slot(team_c, say):
    reply = say("s1", "x", wants_to_book=True,
                doctor_name="Arjun Rao", appointment_date=DAY, appointment_time="9:30")
    assert _state("s1")["state"] == "CONFIRM"
    assert team_c.slot_id("d1", DAY, "09:30") in team_c.held


def test_a_taken_time_offers_the_nearest_free_times(team_c, say):
    team_c.held[team_c.slot_id("d1", DAY, "10:30")] = "someone-else"

    reply = say("s1", "x", wants_to_book=True,
                doctor_name="Arjun Rao", appointment_date=DAY, appointment_time="10:30")

    assert "not available" in reply
    assert "10:00" in reply and "11:00" in reply and "10:30," not in reply
    entry = _state("s1")
    assert entry["state"] == "ASK_TIME"
    assert entry["slots"]["appointment_time"] is None
    # We hold nothing for this patient yet.
    assert [s for s, who in team_c.held.items() if who == "s1"] == []

    # They pick one of the alternatives.
    reply = say("s1", "10:00", appointment_time="10:00")
    assert _state("s1")["state"] == "CONFIRM"
    assert team_c.held[team_c.slot_id("d1", DAY, "10:00")] == "s1"


def test_two_patients_cannot_get_the_same_time(team_c, say):
    first = say("alice", "x", wants_to_book=True, doctor_name="Arjun Rao",
                appointment_date=DAY, appointment_time="10:30")
    second = say("bob", "x", wants_to_book=True, doctor_name="Arjun Rao",
                 appointment_date=DAY, appointment_time="10:30")

    assert "Just to confirm" in first
    assert "not available" in second
    assert _state("bob")["state"] == "ASK_TIME"

    say("alice", "yes", confirms_booking=True)
    assert team_c.booked == {team_c.slot_id("d1", DAY, "10:30")}
    assert len(team_c.appointments) == 1


def test_a_full_day_asks_for_another_date(team_c, say):
    team_c.free_times = []
    reply = say("s1", "x", wants_to_book=True, doctor_name="Arjun Rao",
                appointment_date=DAY, appointment_time="10:30")
    assert "no free appointments" in reply
    entry = _state("s1")
    assert entry["state"] == "ASK_DATE"
    assert entry["slots"]["appointment_date"] is None
    assert entry["slots"]["appointment_time"] is None


def test_a_date_with_no_slots_at_all_is_handled_like_a_full_day(team_c, say):
    reply = say("s1", "x", wants_to_book=True, doctor_name="Arjun Rao",
                appointment_date="2030-03-05", appointment_time="10:30")
    assert "no free appointments" in reply
    assert _state("s1")["state"] == "ASK_DATE"


def test_unknown_doctor_asks_again(team_c, say):
    reply = say("s1", "x", wants_to_book=True, doctor_name="Zzz Nobody",
                appointment_date=DAY, appointment_time="10:30")
    assert "couldn't find a doctor" in reply
    entry = _state("s1")
    assert entry["state"] == "ASK_DOCTOR"
    assert entry["slots"]["doctor_name"] is None
    # Date and time are remembered.
    assert entry["slots"]["appointment_date"] == DAY

    reply = say("s1", "Arjun Rao", doctor_name="Arjun Rao")
    assert _state("s1")["state"] == "CONFIRM"


def test_several_matching_doctors_are_listed_and_a_number_picks_one(team_c, say):
    team_c.doctors = [
        _doctor("d1", "Dr. Arjun Rao", hospital="Zenvy Care Hospital", city="mysore"),
        _doctor("d2", "Dr. Arjun Rao", hospital="Metro Health Hospital", city="bangalore"),
    ]
    reply = say("s1", "x", wants_to_book=True, doctor_name="Arjun Rao",
                appointment_date=DAY, appointment_time="10:30")
    assert "1) Dr. Arjun Rao" in reply and "2) Dr. Arjun Rao" in reply
    assert "Metro Health Hospital" in reply
    assert _state("s1")["state"] == "ASK_DOCTOR"
    assert team_c.held == {}

    reply = say("s1", "the second one", doctor_name=None)
    assert _state("s1")["state"] == "CONFIRM"
    assert _state("s1")["doctor"]["doctor_id"] == "d2"
    assert team_c.held == {team_c.slot_id("d2", DAY, "10:30"): "s1"}


def test_choosing_between_doctors_by_hospital_name(team_c, say):
    team_c.doctors = [
        _doctor("d1", "Dr. Arjun Rao", hospital="Zenvy Care Hospital", city="mysore"),
        _doctor("d2", "Dr. Arjun Rao", hospital="Metro Health Hospital", city="bangalore"),
    ]
    say("s1", "x", wants_to_book=True, doctor_name="Arjun Rao",
        appointment_date=DAY, appointment_time="10:30")
    say("s1", "the one at Metro Health Hospital")
    assert _state("s1")["doctor"]["doctor_id"] == "d2"


def test_doctors_can_be_chosen_by_how_patients_actually_say_the_city(team_c, say):
    team_c.doctors = [
        _doctor("d1", "Dr. Arjun Rao", hospital="Zenvy Care Hospital", city="mysore"),
        _doctor("d2", "Dr. Arjun Rao", hospital="Metro Health Hospital", city="bangalore"),
    ]
    reply = say("s1", "x", wants_to_book=True, doctor_name="Arjun Rao",
                appointment_date=DAY, appointment_time="10:30")
    assert "Mysuru" in reply and "Bengaluru" in reply
    say("s1", "in Bengaluru please")
    assert _state("s1")["doctor"]["doctor_id"] == "d2"


def test_an_unclear_choice_between_doctors_repeats_the_list(team_c, say):
    team_c.doctors = [
        _doctor("d1", "Dr. Arjun Rao", hospital="Zenvy Care Hospital"),
        _doctor("d2", "Dr. Arjun Rao", hospital="Metro Health Hospital"),
    ]
    say("s1", "x", wants_to_book=True, doctor_name="Arjun Rao",
        appointment_date=DAY, appointment_time="10:30")
    reply = say("s1", "umm")
    assert "1) Dr. Arjun Rao" in reply
    assert _state("s1")["state"] == "ASK_DOCTOR"


def test_a_doctor_named_first_is_settled_before_date_and_time_are_asked(team_c, say):
    """The spoken flow: doctor -> which one? -> date -> time -> confirm -> yes."""
    team_c.doctors = [
        _doctor("d1", "Dr. Suresh Reddy", hospital="Kaveri Specialty Hospital", city="mysore"),
        _doctor("d2", "Dr. Suresh Reddy", hospital="Silicon City Specialty Hospital", city="bangalore"),
    ]
    reply = say("s1", "I want to book with Dr Suresh Reddy", wants_to_book=True, doctor_name="Suresh Reddy")
    assert "1) Dr. Suresh Reddy" in reply and "2) Dr. Suresh Reddy" in reply

    reply = say("s1", "The one in Mysuru")
    assert "date" in reply.lower()
    assert _state("s1")["doctor"]["doctor_id"] == "d1"

    reply = say("s1", "tomorrow", appointment_date=DAY)
    assert "time" in reply.lower()
    # Nothing is asked about the doctor again.
    reply = say("s1", "ten thirty", appointment_time="10:30")
    assert "Just to confirm" in reply and "Dr. Suresh Reddy" in reply

    reply = say("s1", "yes please", confirms_booking=True)
    assert "confirmed" in reply.lower()
    assert [a["slot_id"] for a in team_c.appointments] == [team_c.slot_id("d1", DAY, "10:30")]


def test_a_date_said_while_choosing_between_doctors_is_not_lost(team_c, say):
    team_c.doctors = [
        _doctor("d1", "Dr. Suresh Reddy", hospital="Kaveri Specialty Hospital", city="mysore"),
        _doctor("d2", "Dr. Suresh Reddy", hospital="Silicon City Specialty Hospital", city="bangalore"),
    ]
    say("s1", "book Dr Suresh Reddy", wants_to_book=True, doctor_name="Suresh Reddy")

    reply = say("s1", "tomorrow please", appointment_date=DAY)
    assert "1) Dr. Suresh Reddy" in reply  # still needs the doctor
    assert _state("s1")["slots"]["appointment_date"] == DAY

    reply = say("s1", "number two")
    assert _state("s1")["doctor"]["doctor_id"] == "d2"
    assert "time" in reply.lower()  # date was remembered, so it goes straight to the time


def test_a_place_said_with_the_doctor_name_is_used_straight_away(team_c, say):
    team_c.doctors = [
        _doctor("d1", "Dr. Priya Sharma", hospital="Mysuru Multispeciality Hospital", city="mysore"),
        _doctor("d2", "Dr. Priya Sharma", hospital="Bengaluru Multispeciality Centre", city="bangalore"),
    ]
    reply = say("s1", "I need Dr Priya Sharma in Bengaluru", wants_to_book=True, doctor_name="Priya Sharma")
    assert _state("s1")["doctor"]["doctor_id"] == "d2"
    assert "date" in reply.lower()  # no "which doctor?" question


def test_a_stray_number_in_the_first_sentence_never_picks_a_doctor(team_c, say):
    team_c.doctors = [
        _doctor("d1", "Dr. Priya Sharma", hospital="Mysuru Multispeciality Hospital", city="mysore"),
        _doctor("d2", "Dr. Priya Sharma", hospital="Bengaluru Multispeciality Centre", city="bangalore"),
    ]
    reply = say("s1", "I need 1 appointment with Dr Priya Sharma", wants_to_book=True, doctor_name="Priya Sharma")
    assert "1) Dr. Priya Sharma" in reply and "2) Dr. Priya Sharma" in reply
    assert "doctor" not in _state("s1")


def test_an_unknown_doctor_in_the_first_sentence_is_rejected_straight_away(team_c, say):
    reply = say("s1", "book Dr Zzz", wants_to_book=True, doctor_name="Zzz Nobody")
    assert "couldn't find a doctor" in reply
    assert _state("s1")["state"] == "ASK_DOCTOR"


def test_a_department_or_name_alone_never_loops_back_to_the_doctor_question(team_c, say):
    say("s1", "book", wants_to_book=True)
    assert _state("s1")["state"] == "ASK_DOCTOR"
    say("s1", "Arjun Rao", doctor_name="Arjun Rao")
    assert _state("s1")["state"] == "ASK_DATE"
    say("s1", "tomorrow", appointment_date=DAY)
    assert _state("s1")["state"] == "ASK_TIME"
    assert _state("s1")["doctor"]["doctor_id"] == "d1"
    say("s1", "10:30", appointment_time="10:30")
    assert _state("s1")["state"] == "CONFIRM"


def test_saying_no_releases_the_slot_for_others(team_c, say):
    say("alice", "x", wants_to_book=True, doctor_name="Arjun Rao",
        appointment_date=DAY, appointment_time="10:30")
    assert team_c.held

    reply = say("alice", "no", confirms_booking=False)

    assert "cancelled" in reply.lower()
    assert team_c.held == {}
    assert _state("alice") is None
    # Bob can now have it.
    reply = say("bob", "x", wants_to_book=True, doctor_name="Arjun Rao",
                appointment_date=DAY, appointment_time="10:30")
    assert "Just to confirm" in reply


def test_an_unclear_answer_at_confirmation_keeps_the_hold(team_c, say):
    say("s1", "x", wants_to_book=True, doctor_name="Arjun Rao",
        appointment_date=DAY, appointment_time="10:30")
    reply = say("s1", "hmm", confirms_booking=None)
    assert "Just to confirm" in reply
    assert team_c.held == {team_c.slot_id("d1", DAY, "10:30"): "s1"}


def test_if_the_slot_was_lost_before_yes_other_times_are_offered(team_c, say):
    say("alice", "x", wants_to_book=True, doctor_name="Arjun Rao",
        appointment_date=DAY, appointment_time="10:30")
    slot = team_c.slot_id("d1", DAY, "10:30")
    # Alice's hold ran out and Bob took the slot.
    team_c.held[slot] = "bob"

    reply = say("alice", "yes", confirms_booking=True)

    assert "not available" in reply
    assert team_c.appointments == []
    assert _state("alice")["state"] == "ASK_TIME"


def test_a_booking_error_releases_the_slot_and_apologises(team_c, say, monkeypatch):
    say("s1", "x", wants_to_book=True, doctor_name="Arjun Rao",
        appointment_date=DAY, appointment_time="10:30")

    def boom(**kwargs):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(state_machine, "create_appointment", boom)
    reply = say("s1", "yes", confirms_booking=True)

    assert "couldn't complete the booking" in reply
    assert team_c.held == {}
    assert _state("s1") is None


def test_team_c_being_down_during_lookup_apologises_and_clears_state(team_c, say):
    team_c.down = True
    reply = say("s1", "x", wants_to_book=True, doctor_name="Arjun Rao",
                appointment_date=DAY, appointment_time="10:30")
    assert "couldn't complete the booking" in reply
    assert _state("s1") is None


def test_a_saved_booking_whose_whatsapp_failed_is_still_reported_as_booked(team_c, say):
    team_c.notification_fails = True
    say("s1", "x", wants_to_book=True, doctor_name="Arjun Rao",
        appointment_date=DAY, appointment_time="10:30")
    reply = say("s1", "yes", confirms_booking=True)
    assert "confirmed" in reply.lower()
    assert len(team_c.appointments) == 1


def test_replies_come_in_kannada_and_hindi(team_c, say):
    team_c.held[team_c.slot_id("d1", DAY, "10:30")] = "someone-else"
    kannada = say("k", "x", lang="kn", wants_to_book=True, doctor_name="Arjun Rao",
                  appointment_date=DAY, appointment_time="10:30")
    hindi = say("h", "x", lang="hi", wants_to_book=True, doctor_name="Arjun Rao",
                appointment_date=DAY, appointment_time="10:30")
    assert any("ಀ" <= ch <= "೿" for ch in kannada)
    assert any("ऀ" <= ch <= "ॿ" for ch in hindi)
    assert "10:00" in kannada and "10:00" in hindi


def test_changing_the_doctor_mid_booking_searches_again(team_c, say):
    team_c.doctors.append(_doctor("d2", "Dr. Priya Sharma", specialty="Dermatologist"))
    say("s1", "x", wants_to_book=True, doctor_name="Arjun Rao",
        appointment_date=DAY, appointment_time="10:30")
    # They say no to the confirmation; a fresh request names someone else.
    say("s1", "no", confirms_booking=False)
    say("s1", "x", wants_to_book=True, doctor_name="Priya Sharma",
        appointment_date=DAY, appointment_time="10:30")
    assert _state("s1")["doctor"]["doctor_id"] == "d2"


# ---------------------------------------------------------------------------
# conversation_client: how the HTTP responses from Team C are interpreted
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")


def test_client_create_appointment_sends_only_the_slot(monkeypatch):
    sent = {}

    def fake_post(url, json=None, timeout=None):
        sent["url"], sent["json"] = url, json
        return FakeResponse(201, {"appointment_id": "a1"})

    monkeypatch.setattr(requests, "post", fake_post)
    result = conversation_client.create_appointment(
        session_id="s", patient_uhid="U", slot_id="slot-1", status="confirmed"
    )
    assert result == {"appointment_id": "a1"}
    assert sent["url"].endswith("/api/v1/appointments")
    assert sent["json"]["slot_id"] == "slot-1"
    assert "doctor_name" not in sent["json"] and "appointment_datetime" not in sent["json"]


def test_client_create_appointment_without_slot_still_sends_doctor_and_time(monkeypatch):
    sent = {}
    monkeypatch.setattr(
        requests, "post",
        lambda url, json=None, timeout=None: sent.update(json=json) or FakeResponse(201, {}),
    )
    conversation_client.create_appointment(
        session_id="s", patient_uhid="U", doctor_name="Dr X",
        appointment_datetime="2030-01-01T10:00:00+05:30",
    )
    assert sent["json"]["doctor_name"] == "Dr X"
    assert "slot_id" not in sent["json"]


def test_client_409_becomes_slot_unavailable(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(409, text="slot_unavailable"))
    with pytest.raises(SlotUnavailableError):
        conversation_client.create_appointment(session_id="s", patient_uhid="U", slot_id="x")
    with pytest.raises(SlotUnavailableError):
        conversation_client.hold_slot("x", "s")


def test_client_treats_saved_but_whatsapp_failed_as_success(monkeypatch):
    monkeypatch.setattr(
        requests, "post",
        lambda *a, **k: FakeResponse(
            502, text='{"detail":"Appointment saved, but WhatsApp notification failed"}'
        ),
    )
    result = conversation_client.create_appointment(session_id="s", patient_uhid="U", slot_id="x")
    assert result == {"notification_failed": True}


def test_client_other_errors_still_raise(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(500, text="boom"))
    with pytest.raises(requests.exceptions.HTTPError):
        conversation_client.create_appointment(session_id="s", patient_uhid="U", slot_id="x")


def test_client_slot_listing_passes_near_and_limit(monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen["url"], seen["params"] = url, params
        return FakeResponse(200, [])

    monkeypatch.setattr(requests, "get", fake_get)
    conversation_client.get_free_slots("d1", DAY, near="10:30", limit=3)
    assert seen["url"].endswith("/api/v1/doctors/d1/slots")
    assert seen["params"] == {"date": DAY, "near": "10:30", "limit": 3}
    conversation_client.get_free_slots("d1", DAY)
    assert seen["params"] == {"date": DAY}


@pytest.mark.parametrize("answer", [
    "Yes", "yes.", "Yeah", "okay", "Sure, go ahead", "book it", "haan", "हाँ", "ठीक है", "ಹೌದು", "ಸರಿ",
])
def test_a_plain_yes_confirms_even_if_the_nlu_returns_nothing(team_c, say, answer):
    say("s1", "x", wants_to_book=True, doctor_name="Arjun Rao",
        appointment_date=DAY, appointment_time="10:30")
    reply = say("s1", answer, confirms_booking=None)
    assert "confirmed" in reply.lower()
    assert len(team_c.appointments) == 1


@pytest.mark.parametrize("answer", ["No", "nope", "cancel it", "don't book", "nahi", "नहीं", "ಬೇಡ"])
def test_a_plain_no_cancels_even_if_the_nlu_returns_nothing(team_c, say, answer):
    say("s1", "x", wants_to_book=True, doctor_name="Arjun Rao",
        appointment_date=DAY, appointment_time="10:30")
    reply = say("s1", answer, confirms_booking=None)
    assert "cancelled" in reply.lower()
    assert team_c.held == {} and team_c.appointments == []


@pytest.mark.parametrize("answer", ["hmm", "what time was it", "yes no", ""])
def test_an_unclear_answer_does_not_book_or_cancel(team_c, say, answer):
    say("s1", "x", wants_to_book=True, doctor_name="Arjun Rao",
        appointment_date=DAY, appointment_time="10:30")
    reply = say("s1", answer, confirms_booking=None)
    assert "Just to confirm" in reply
    assert team_c.appointments == [] and len(team_c.held) == 1


# ---------------------------------------------------------------------------
# "today" is the hospital's day (India), not the server's UTC day
# ---------------------------------------------------------------------------

def test_today_is_the_indian_date_even_while_utc_is_still_yesterday():
    from datetime import datetime, timezone
    from services.orchestrator.entity_extraction import hospital_today

    # 19:21 UTC on the 8th is 00:51 on the 9th in India.
    assert hospital_today(datetime(2026, 10, 8, 19, 21, tzinfo=timezone.utc)).isoformat() == "2026-10-09"
    # 18:29 UTC is still 23:59 on the 8th in India.
    assert hospital_today(datetime(2026, 10, 8, 18, 29, tzinfo=timezone.utc)).isoformat() == "2026-10-08"
    # Midday agrees on both clocks.
    assert hospital_today(datetime(2026, 10, 8, 6, 0, tzinfo=timezone.utc)).isoformat() == "2026-10-08"


def test_the_language_model_is_told_the_indian_date(monkeypatch):
    import services.orchestrator.entity_extraction as extraction

    monkeypatch.setattr(extraction, "hospital_today", lambda now=None: __import__("datetime").date(2026, 10, 9))
    sent = {}

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(extraction.requests, "post", lambda url, headers=None, json=None, timeout=None: sent.update(json=json) or Reply())
    extraction.extract_booking_fields("book tomorrow")
    assert "2026-10-09" in sent["json"]["messages"][0]["content"]
