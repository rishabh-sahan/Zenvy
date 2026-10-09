"""
Cancelling and rescheduling by chat or voice (the same conversation brain).

Team C is a small in-memory fake that behaves like the real one (changeable
appointments, held slots, refusals); Redis is a dict; the NLU is scripted.
"""
import os
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
_dummy_key = not os.environ.get("SARVAM_API_KEY")
if _dummy_key:
    os.environ["SARVAM_API_KEY"] = "dummy-key-for-import"

from services.conversation_client import ChangeRefused, NotLoggedIn, SlotUnavailableError
from services.orchestrator import state_machine
from tests.test_booking_slots import DAY, FakeRedis, FakeTeamC, IST

if _dummy_key:
    del os.environ["SARVAM_API_KEY"]

AUTH = "auth-1"
TODAY = date(2030, 3, 1)   # "DAY" (2030-03-04) is a Monday three days later


def appointment(appointment_id, doctor_name="Dr. Arjun Rao", doctor_id="d1", when="2030-03-04T10:00:00+05:30", **extra):
    return {
        "appointment_id": appointment_id, "doctor_id": doctor_id, "doctor_name": doctor_name,
        "hospital_name": "Zenvy Care Hospital", "appointment_datetime": when,
        "appointment_type": "new", "can_change": True, "change_blocker": None, "status": "confirmed", **extra,
    }


class FakeTeamCWithAppointments(FakeTeamC):
    def __init__(self):
        super().__init__()
        self.user_id = AUTH
        self.appointments_of_patient = [appointment("apt-1")]
        self.cancel_calls, self.reschedule_calls = [], []
        self.refuse_with = None
        self.crash = False

    # --- what the orchestrator imports ----------------------------------------
    def get_session(self, session_id):
        return {"session_id": session_id, "user_id": self.user_id}

    def list_my_appointments(self, auth_id):
        if self.crash:
            raise RuntimeError("team c down")
        if auth_id in (None, "nobody"):
            raise NotLoggedIn(auth_id)
        return [deepcopy(a) for a in self.appointments_of_patient if a["status"] != "cancelled"]

    def cancel_my_appointment(self, appointment_id, auth_id, reason="patient_request"):
        if self.crash:
            raise RuntimeError("team c down")
        if self.refuse_with:
            raise ChangeRefused(self.refuse_with)
        self.cancel_calls.append((appointment_id, auth_id))
        next(a for a in self.appointments_of_patient if a["appointment_id"] == appointment_id)["status"] = "cancelled"
        return {}

    def reschedule_my_appointment(self, appointment_id, slot_id, auth_id, session_id=None):
        if self.crash:
            raise RuntimeError("team c down")
        if self.refuse_with:
            raise ChangeRefused(self.refuse_with)
        if self.held.get(slot_id) != session_id:
            raise SlotUnavailableError(slot_id)
        self.reschedule_calls.append((appointment_id, slot_id, auth_id, session_id))
        del self.held[slot_id]
        self.booked.add(slot_id)
        old = next(a for a in self.appointments_of_patient if a["appointment_id"] == appointment_id)
        old["status"] = "cancelled"
        doctor_id, day, hhmm = slot_id.split("|")
        self.appointments_of_patient.append(appointment("apt-new", old["doctor_name"], doctor_id, f"{day}T{hhmm}:00+05:30"))
        return {}


@pytest.fixture
def team_c(monkeypatch):
    fake = FakeTeamCWithAppointments()
    monkeypatch.setattr(state_machine, "redis_client", FakeRedis())
    monkeypatch.setattr(state_machine, "hospital_today", lambda: TODAY)
    for name in ("find_doctors", "get_free_slots", "hold_slot", "release_slot", "create_appointment",
                 "get_session", "list_my_appointments", "cancel_my_appointment", "reschedule_my_appointment"):
        monkeypatch.setattr(state_machine, name, getattr(fake, name))
    monkeypatch.setattr(state_machine, "generate_reply", lambda text, lang: f"[Q&A] {text}")
    return fake


@pytest.fixture
def say(monkeypatch):
    def _say(text, lang="en", session="s1", **fields):
        extracted = {
            "intent": "Unclear", "wants_to_book": False, "doctor_name": None, "appointment_date": None,
            "appointment_time": None, "confirms_booking": None,
        }
        extracted.update(fields)
        monkeypatch.setattr(state_machine, "extract_booking_fields", lambda _text: extracted)
        return state_machine.handle_turn(session, lang, text)

    return _say


def state(session="s1"):
    return state_machine._get_session_state(session)


# ---------------------------------------------------------------------------
# cancelling
# ---------------------------------------------------------------------------

def test_cancelling_the_only_appointment_asks_then_cancels(team_c, say):
    reply = say("I want to cancel my appointment")
    assert "cancel your appointment with Dr. Arjun Rao on Mon 04 Mar at 10:00" in reply
    assert state()["state"] == "CANCEL_CONFIRM" and team_c.cancel_calls == []

    reply = say("yes")
    assert "Done" in reply and "cancelled" in reply
    assert team_c.cancel_calls == [("apt-1", AUTH)]
    assert state() is None


def test_saying_no_keeps_the_appointment(team_c, say):
    say("cancel my appointment")
    reply = say("no")
    assert "kept" in reply and team_c.cancel_calls == [] and state() is None


def test_an_unclear_answer_asks_again_and_cancels_nothing(team_c, say):
    say("cancel my appointment")
    assert "Do you want to cancel" in say("hmm")
    assert "Do you want to cancel" in say("what time was it")
    assert team_c.cancel_calls == []
    assert "Done" in say("yes please")


@pytest.mark.parametrize("answer", ["no, make it 11 am please", "yes but later", "cancel it tomorrow instead"])
def test_a_change_of_mind_is_not_taken_as_a_yes(team_c, say, answer):
    say("cancel my appointment")
    say(answer)
    assert team_c.cancel_calls == []


@pytest.mark.parametrize("answer", ["Yes, cancel it.", "yes cancel it", "cancel it", "ok cancel"])
def test_yes_with_the_word_cancel_is_a_yes_even_if_the_model_says_no(team_c, say, answer):
    """Found by voice: 'Yes, cancel it.' has a yes word and a no word and was kept."""
    say("cancel my appointment")
    assert "Done" in say(answer, confirms_booking=False)
    assert len(team_c.cancel_calls) == 1


def test_dont_cancel_it_keeps_the_appointment(team_c, say):
    say("cancel my appointment")
    assert "kept" in say("no, don't cancel it", confirms_booking=True)
    assert team_c.cancel_calls == []


def test_a_plain_yes_works_even_if_the_model_returns_nothing(team_c, say):
    say("cancel my appointment")
    assert "Done" in say("Yes", confirms_booking=None)


@pytest.mark.parametrize("sentence", [
    "cancel my appointment", "Cancel", "I need to cancel the appointment", "please cancel it",
    "cancelling my booking", "call off my appointment", "I don't want my appointment anymore",
])
def test_many_ways_of_asking_to_cancel(team_c, say, sentence):
    assert "cancel your appointment with" in say(sentence)


@pytest.mark.parametrize("sentence", ["don't cancel my appointment", "I do not want to cancel", "no need to cancel"])
def test_a_negated_request_is_not_a_cancellation(team_c, say, sentence):
    reply = say(sentence)
    assert reply.startswith("[Q&A]") and state() is None


def test_with_several_appointments_it_lists_them_and_the_number_picks_one(team_c, say):
    team_c.appointments_of_patient = [
        appointment("apt-1", "Dr. Arjun Rao", when="2030-03-04T10:00:00+05:30"),
        appointment("apt-2", "Dr. Priya Sharma", doctor_id="d2", when="2030-03-06T15:30:00+05:30"),
    ]
    reply = say("cancel my appointment")
    assert "1) Dr. Arjun Rao on Mon 04 Mar at 10:00" in reply and "2) Dr. Priya Sharma on Wed 06 Mar at 15:30" in reply
    assert state()["state"] == "CANCEL_PICK"

    reply = say("the second one")
    assert "Priya Sharma" in reply and state()["state"] == "CANCEL_CONFIRM"
    assert "Done" in say("yes")
    assert team_c.cancel_calls == [("apt-2", AUTH)]


@pytest.mark.parametrize("answer,expected", [
    ("1", "apt-1"), ("number two", "apt-2"), ("first", "apt-1"), ("the last one", "apt-2"),
    ("Priya Sharma", "apt-2"), ("the one with Arjun", "apt-1"), ("the one on 6 march", "apt-2"),
    ("the 15:30 one", "apt-2"),
])
def test_many_ways_of_saying_which_appointment(team_c, say, answer, expected):
    team_c.appointments_of_patient = [
        appointment("apt-1", "Dr. Arjun Rao", when="2030-03-04T10:00:00+05:30"),
        appointment("apt-2", "Dr. Priya Sharma", doctor_id="d2", when="2030-03-06T15:30:00+05:30"),
    ]
    say("cancel my appointment")
    say(answer)
    assert state()["target"]["appointment_id"] == expected


def test_an_unclear_choice_repeats_the_list(team_c, say):
    team_c.appointments_of_patient = [appointment("apt-1"), appointment("apt-2", "Dr. Priya Sharma", "d2", "2030-03-06T15:30:00+05:30")]
    say("cancel my appointment")
    reply = say("umm")
    assert "1) Dr. Arjun Rao" in reply and state()["state"] == "CANCEL_PICK"


def test_never_mind_while_choosing_stops_everything(team_c, say):
    team_c.appointments_of_patient = [appointment("apt-1"), appointment("apt-2", "Dr. Priya Sharma", "d2", "2030-03-06T15:30:00+05:30")]
    say("cancel my appointment")
    assert "kept" in say("never mind")
    assert state() is None


def test_only_appointments_that_can_still_be_changed_are_offered(team_c, say):
    team_c.appointments_of_patient = [
        appointment("apt-1", can_change=False, change_blocker="has_consultation"),
        appointment("apt-2", "Dr. Priya Sharma", "d2", "2030-03-06T15:30:00+05:30"),
    ]
    reply = say("cancel my appointment")
    assert "Priya Sharma" in reply and "Arjun" not in reply      # one left: straight to the question
    assert state()["target"]["appointment_id"] == "apt-2"


def test_if_none_can_be_changed_the_patient_is_told_why(team_c, say):
    team_c.appointments_of_patient = [appointment("apt-1", can_change=False, change_blocker="already_started")]
    assert "can't be changed any more" in say("cancel my appointment")
    assert state() is None


def test_no_appointments_and_not_logged_in(team_c, say):
    team_c.appointments_of_patient = []
    assert "no upcoming appointments" in say("cancel my appointment")
    team_c.user_id = "nobody"
    assert "log in first" in say("cancel my appointment")
    team_c.user_id = None
    assert "log in first" in say("cancel my appointment")


def test_team_c_refusing_at_the_last_moment_is_explained(team_c, say):
    say("cancel my appointment")
    team_c.refuse_with = "has_consultation"
    assert "can't be changed any more" in say("yes")
    assert state() is None


def test_a_server_problem_is_apologised_for_and_clears_the_state(team_c, say):
    team_c.crash = True
    assert "system issue" in say("cancel my appointment")
    assert state() is None


def test_the_cancel_request_is_understood_in_hindi_and_kannada(team_c, say):
    hindi = say("मुझे अपनी अपॉइंटमेंट रद्द करनी है", lang="hi", session="h")
    assert "रद्द करना चाहते हैं" in hindi
    kannada = say("ನನ್ನ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ರದ್ದು ಮಾಡಿ", lang="kn", session="k")
    assert "ರದ್ದುಗೊಳಿಸಲೇ" in kannada
    assert "रद्द कर दी गई" in say("हाँ", lang="hi", session="h")
    assert team_c.cancel_calls == [("apt-1", AUTH)]


def test_the_model_alone_can_start_a_change_conversation_by_asking_which(team_c, say):
    reply = say("something about my booking", intent="Cancel/Reschedule")
    assert "cancel your appointment or change its time" in reply
    assert state()["state"] == "CHOOSE_ACTION"
    assert "cancel your appointment with" in say("cancel it")


def test_cancel_or_reschedule_in_one_sentence_asks_which(team_c, say):
    assert "cancel your appointment or change its time" in say("should I cancel or reschedule my appointment")


def test_when_asked_which_the_patient_can_pick_reschedule_or_walk_away(team_c, say):
    say("change my booking", intent="Cancel/Reschedule")
    assert "What new date" in say("reschedule")
    say("never mind")
    say("change my booking", intent="Cancel/Reschedule", session="s2")
    assert "kept" in say("never mind", session="s2")
    assert "cancel your appointment or change" in say("hmm", session="s3", intent="Cancel/Reschedule")


# ---------------------------------------------------------------------------
# rescheduling
# ---------------------------------------------------------------------------

def test_rescheduling_asks_for_a_new_date_and_time_then_confirms(team_c, say):
    reply = say("I want to reschedule my appointment")
    assert "Your appointment with Dr. Arjun Rao is on Mon 04 Mar at 10:00" in reply and "new date" in reply

    assert "time" in say("on 4th March", appointment_date=DAY).lower()
    reply = say("10:30", appointment_time="10:30")
    assert f"move your appointment with Dr. Arjun Rao from Mon 04 Mar at 10:00 to {DAY} at 10:30" in reply
    # the new time is held while the patient decides
    assert team_c.held == {team_c.slot_id("d1", DAY, "10:30"): "s1"}
    assert team_c.reschedule_calls == []


def test_a_full_reschedule_calls_team_c_once_with_the_new_slot_and_ends_the_flow(team_c, say):
    say("reschedule my appointment")
    say("tomorrow", appointment_date=DAY)
    say("11:00", appointment_time="11:00")
    reply = say("yes")

    slot_id = team_c.slot_id("d1", DAY, "11:00")
    assert team_c.reschedule_calls == [("apt-1", slot_id, AUTH, "s1")]
    assert "Done" in reply and f"now on {DAY} at 11:00" in reply
    assert state() is None and team_c.held == {}
    # the old appointment is gone and a new one exists
    assert [a["appointment_id"] for a in team_c.appointments_of_patient if a["status"] != "cancelled"] == ["apt-new"]


def test_everything_in_one_sentence_goes_straight_to_the_confirmation(team_c, say):
    reply = say("reschedule my appointment to 4th March at 11 am")
    assert "move your appointment" in reply and "11:00" in reply
    assert team_c.held == {team_c.slot_id("d1", DAY, "11:00"): "s1"}


def test_the_time_is_read_by_rules_when_the_model_finds_nothing(team_c, say):
    say("reschedule my appointment")
    say("4th March")                    # the model returns nothing; the rules read the date
    assert state()["slots"]["appointment_date"] == DAY and state()["state"] == "ASK_TIME"
    assert "move your appointment" in say("11")
    assert team_c.held == {team_c.slot_id("d1", DAY, "11:00"): "s1"}


def test_a_taken_time_offers_other_times_and_keeps_the_old_appointment(team_c, say):
    team_c.held[team_c.slot_id("d1", DAY, "11:00")] = "someone-else"
    say("reschedule my appointment")
    reply = say("4th March at 11:00", appointment_date=DAY, appointment_time="11:00")
    assert "not available" in reply and "10:30" in reply and "11:30" in reply
    assert team_c.reschedule_calls == [] and state()["state"] == "ASK_TIME"
    assert "move your appointment" in say("10:30", appointment_time="10:30")


def test_saying_no_at_the_end_keeps_the_appointment_and_releases_the_new_time(team_c, say):
    say("reschedule my appointment")
    say("4th March at 11:00", appointment_date=DAY, appointment_time="11:00")
    assert team_c.held
    reply = say("no")
    assert "kept your appointment as it was" in reply
    assert team_c.held == {} and team_c.reschedule_calls == [] and state() is None


def test_never_mind_while_picking_the_new_date_stops_the_reschedule(team_c, say):
    say("reschedule my appointment")
    assert "kept your appointment as it was" in say("never mind")
    assert state() is None


def test_the_doctor_cannot_be_changed_during_a_reschedule(team_c, say):
    team_c.doctors.append({"doctor_id": "d2", "name": "Dr. Priya Sharma", "specialty": "Dermatologist", "hospital_name": "H", "city": "mysore", "slot_minutes": 30})
    say("reschedule my appointment")
    say("with Dr. Priya Sharma on 4th March", doctor_name="Priya Sharma", appointment_date=DAY)
    assert state()["doctor"]["doctor_id"] == "d1"       # still the original doctor
    assert state()["state"] == "ASK_TIME"


def test_team_c_refusing_a_reschedule_explains_and_frees_the_new_time(team_c, say):
    say("reschedule my appointment")
    say("4th March at 11:00", appointment_date=DAY, appointment_time="11:00")
    team_c.refuse_with = "already_started"
    assert "can't be changed any more" in say("yes")
    assert team_c.held == {} and state() is None


def test_the_new_time_being_lost_at_the_last_moment_offers_other_times(team_c, say):
    say("reschedule my appointment")
    say("4th March at 11:00", appointment_date=DAY, appointment_time="11:00")
    team_c.held[team_c.slot_id("d1", DAY, "11:00")] = "someone-else"       # lost the hold
    reply = say("yes")
    assert "not available" in reply and team_c.reschedule_calls == []
    assert state()["flow"] == "reschedule" and state()["state"] == "ASK_TIME"


def test_with_several_appointments_the_patient_picks_which_to_move(team_c, say):
    team_c.appointments_of_patient = [
        appointment("apt-1", when="2030-03-04T10:00:00+05:30"),
        appointment("apt-2", "Dr. Arjun Rao", when="2030-03-08T09:30:00+05:30"),
    ]
    reply = say("reschedule my appointment")
    assert "Which appointment would you like to move" in reply
    reply = say("2")
    assert "Fri 08 Mar at 09:30" in reply and "new date" in reply
    say("4th March at 11:00", appointment_date=DAY, appointment_time="11:00")
    say("yes")
    assert team_c.reschedule_calls[0][0] == "apt-2"


def test_an_appointment_without_a_doctor_slot_cannot_be_rescheduled_but_can_be_cancelled(team_c, say):
    team_c.appointments_of_patient = [appointment("apt-1", doctor_id=None)]
    assert "can't be changed any more" in say("reschedule my appointment")
    assert "cancel your appointment with" in say("cancel my appointment", session="s2")


@pytest.mark.parametrize("sentence", [
    "reschedule my appointment", "I want to postpone my appointment", "can I change my appointment",
    "move my appointment to another day", "please re-schedule it", "I need to change the time of my appointment",
])
def test_many_ways_of_asking_to_reschedule(team_c, say, sentence):
    assert "new date" in say(sentence)


def test_the_reschedule_request_is_understood_in_hindi_and_kannada(team_c, say):
    assert "नई तारीख" in say("मेरी अपॉइंटमेंट का समय बदलना है", lang="hi", session="h")
    assert "ಹೊಸ ದಿನಾಂಕ" in say("ನನ್ನ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಸಮಯ ಬದಲಾಯಿಸಬೇಕು", lang="kn", session="k")


# ---------------------------------------------------------------------------
# the ordinary booking flow is untouched
# ---------------------------------------------------------------------------

def test_a_normal_question_is_still_answered_normally(team_c, say):
    assert say("what are the visiting hours?").startswith("[Q&A]")


def test_a_normal_booking_still_works_end_to_end(team_c, say):
    say("book", wants_to_book=True, doctor_name="Arjun Rao", appointment_date=DAY, appointment_time="10:30")
    reply = say("yes", confirms_booking=True)
    assert "confirmed" in reply.lower() and len(team_c.appointments) == 1


def test_never_mind_in_the_middle_of_a_booking_stops_it_and_frees_the_slot(team_c, say):
    say("book", wants_to_book=True, doctor_name="Arjun Rao", appointment_date=DAY)
    assert state()["state"] == "ASK_TIME"
    assert "cancelled that booking request" in say("never mind")
    assert state() is None

    say("book", wants_to_book=True, doctor_name="Arjun Rao", appointment_date=DAY, appointment_time="10:30", session="s2")
    assert team_c.held                                           # held for the confirmation
    # "cancel" at the confirmation step is a no (the existing behaviour)
    assert "cancelled that booking request" in say("cancel", session="s2")
    assert team_c.held == {}


def test_cancel_inside_a_booking_does_not_start_a_cancellation_of_a_real_appointment(team_c, say):
    say("book", wants_to_book=True, doctor_name="Arjun Rao")
    say("cancel")
    assert team_c.cancel_calls == []
