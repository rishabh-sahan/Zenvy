"""The patient agent: medicine questions by chat and voice, and "I took it"."""
from datetime import datetime, timedelta, timezone

import pytest
import requests

from services.agents import patient_agent
from services.conversation_client import NotLoggedIn
from services.orchestrator import state_machine
from tests.test_booking_slots import FakeRedis, FakeTeamC, _state, say, team_c  # noqa: F401  (fixtures)

IST = timezone(timedelta(hours=5, minutes=30))
AUTH = "auth-1"

PARA = {"drug_name": "Paracetamol", "strength": "500 mg", "dose_text": "1 tablet", "dose_times": ["08:00", "21:00"],
        "food": "after", "as_needed": False, "next_dose_at": "2030-03-04T21:00:00+05:30"}
SYRUP = {"drug_name": "Cough syrup", "strength": None, "dose_text": "10 ml", "dose_times": ["08:00", "14:00", "21:00"],
         "food": "any", "as_needed": False, "next_dose_at": "2030-03-04T14:00:00+05:30"}
ANTACID = {"drug_name": "Antacid", "dose_text": None, "dose_times": [], "food": "any", "as_needed": True, "next_dose_at": None}


@pytest.fixture
def agent(monkeypatch):
    """A patient agent whose Team C is a few lists."""
    state = {"items": [PARA, SYRUP], "taken": {"taken": 1, "medicines": ["Paracetamol"]}, "down": False, "actions": []}

    def meds(auth_id):
        if auth_id == "stranger":
            raise NotLoggedIn(auth_id)
        if state["down"]:
            raise requests.exceptions.ConnectionError("down")
        return {"prescriptions": [{"prescription_id": "p1", "items": state["items"]}] if state["items"] else [], "messages": []}

    def taken(auth_id):
        if state["down"]:
            raise requests.exceptions.ConnectionError("down")
        return state["taken"]

    monkeypatch.setattr(patient_agent.conversation_client, "get_my_medications", meds)
    monkeypatch.setattr(patient_agent.conversation_client, "mark_my_doses_taken", taken)
    monkeypatch.setattr(patient_agent.conversation_client, "record_agent_action",
                        lambda agent, tool, summary=None, **k: state["actions"].append((agent, tool, summary)))
    monkeypatch.setattr(state_machine, "_patient_id", lambda session_id: AUTH)
    return state


# ---------------------------------------------------------------------------
# recognising the questions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,intent", [
    ("what medicines do I take", "list"), ("What are my medicines?", "list"), ("show my tablets", "list"),
    ("my prescription", "list"), ("medication list please", "list"), ("what am I taking", "list"),
    ("when is my next dose", "next"), ("when should I take my tablet", "next"), ("next medicine?", "next"),
    ("I took my medicine", "taken"), ("I have taken my tablets", "taken"), ("just took my pill", "taken"),
    ("मैंने दवा ले ली", "taken"), ("मेरी दवा कौन सी है", "list"), ("अगली खुराक कब है", "next"),
    ("ನನ್ನ ಔಷಧ ಯಾವುದು", "list"),
])
def test_medicine_questions_are_recognised(text, intent):
    assert patient_agent.detect_medicine_intent(text) == intent


@pytest.mark.parametrize("text", [
    "", "book an appointment with Dr Rao", "medicine for fever", "what medicine should I take for a headache",
    "can I take paracetamol", "I took the bus here", "I am taking my mother to the hospital", "what time do you open",
    "cancel my appointment",
])
def test_everything_else_is_left_to_the_normal_assistant(text):
    assert patient_agent.detect_medicine_intent(text) is None


# ---------------------------------------------------------------------------
# answering
# ---------------------------------------------------------------------------

def test_the_list_repeats_exactly_what_the_doctor_wrote(agent):
    reply = patient_agent.handle(AUTH, "en", "list")
    assert reply == ("Your medicines: Paracetamol 500 mg (1 tablet, 08:00 and 21:00, after food); "
                     "Cough syrup (10 ml, 08:00, 14:00 and 21:00). Please follow your doctor's instructions.")


def test_an_only_when_needed_medicine_is_described_as_such(agent):
    agent["items"] = [ANTACID]
    assert "Antacid (only when needed)" in patient_agent.handle(AUTH, "en", "list")


def test_the_next_dose_is_the_soonest_one(agent):
    now = datetime(2030, 3, 4, 9, 0, tzinfo=IST)
    assert patient_agent.handle(AUTH, "en", "next", now) == "Your next dose is Cough syrup, 10 ml, today at 14:00."


def test_a_dose_tomorrow_or_later_says_so(agent):
    agent["items"] = [{**PARA, "next_dose_at": "2030-03-05T08:00:00+05:30"}]
    now = datetime(2030, 3, 4, 22, 0, tzinfo=IST)
    assert patient_agent.handle(AUTH, "en", "next", now).endswith("tomorrow at 08:00.")
    agent["items"] = [{**PARA, "next_dose_at": "2030-03-09T08:00:00+05:30"}]
    assert patient_agent.handle(AUTH, "en", "next", now).endswith("on Sat 09 Mar at 08:00.")


def test_no_upcoming_dose_and_no_medicines_have_their_own_answers(agent):
    agent["items"] = [ANTACID]
    assert patient_agent.handle(AUTH, "en", "next") == "You have no upcoming doses scheduled."
    agent["items"] = []
    assert patient_agent.handle(AUTH, "en", "list") == "I don't see any medicines prescribed by your doctor right now."


def test_marking_a_dose_taken_names_what_was_marked(agent):
    agent["taken"] = {"taken": 2, "medicines": ["Paracetamol", "Cough syrup"]}
    assert patient_agent.handle(AUTH, "en", "taken") == "Noted. I've marked Paracetamol, Cough syrup as taken."
    agent["taken"] = {"taken": 0, "medicines": []}
    assert "don't see a dose due" in patient_agent.handle(AUTH, "en", "taken")


def test_a_stranger_or_nobody_is_asked_to_log_in(agent):
    assert "log in" in patient_agent.handle(None, "en", "list")
    assert "log in" in patient_agent.handle("stranger", "en", "list")


def test_when_the_hospital_service_is_down_the_agent_says_so_and_does_not_crash(agent):
    agent["down"] = True
    for intent in ("list", "next", "taken"):
        assert "couldn't check your medicines" in patient_agent.handle(AUTH, "en", intent)


def test_other_languages_have_every_reply():
    from services.orchestrator.templates import TEMPLATES

    for key in ("MED_NOT_LOGGED_IN", "MED_NONE", "MED_LIST", "MED_NEXT", "MED_NEXT_NONE", "MED_TAKEN", "MED_TAKEN_NONE", "MED_FAILED"):
        assert set(TEMPLATES[key]) == {"en", "hi", "kn"}, key
    assert "लॉग इन" in patient_agent.handle(None, "hi", "list")


def test_every_lookup_is_in_the_audit_trail_without_the_medicine_names(agent):
    patient_agent.handle(AUTH, "en", "list")
    patient_agent.handle(AUTH, "en", "taken")
    assert agent["actions"] == [("patient", "medicines_list", "2 medicine(s)"), ("patient", "mark_dose_taken", "1 dose(s)")]


# ---------------------------------------------------------------------------
# through the conversation
# ---------------------------------------------------------------------------

def test_the_conversation_hands_a_medicine_question_to_the_patient_agent(agent, team_c, say):
    reply = say("s1", "what medicines do I take?")
    assert reply.startswith("Your medicines: Paracetamol 500 mg")


def test_the_conversation_still_books_appointments_as_before(agent, team_c, say):
    reply = say("s1", "book an appointment with Dr Arjun Rao", wants_to_book=True, doctor_name="Arjun Rao")
    assert "date" in reply.lower()
    assert _state("s1")["state"] == "ASK_DATE"


def test_a_medicine_question_in_the_middle_of_a_booking_does_not_break_it(agent, team_c, say):
    say("s1", "book an appointment with Dr Arjun Rao", wants_to_book=True, doctor_name="Arjun Rao")
    reply = say("s1", "what medicines do I take?")
    assert "date" in reply.lower()                            # the booking carries on asking
    assert _state("s1")["state"] == "ASK_DATE"
