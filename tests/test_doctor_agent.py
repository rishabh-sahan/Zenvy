"""Medicine extraction from the transcript, the doctor agent, and the gateway endpoints that expose them."""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.agents import doctor_agent
from services.gateway import doctor_routes
from services.scribe import medication, summarize
from services.scribe.team_c import TeamCError

TURNS = [
    {"speaker": "doctor", "text": "You have a viral fever. Take Paracetamol 500 mg twice a day after food for three days."},
    {"speaker": "patient", "text": "Okay doctor. I also take Telma for my blood pressure."},
    {"speaker": "doctor", "text": "Continue that. And a cough syrup, ten ml thrice a day, only if the cough bothers you."},
]
PLAN = "Paracetamol 500 mg twice a day after food for 3 days. Cough syrup 10 ml as needed."


def reply_with(*medicines):
    return lambda messages: json.dumps({"medicines": list(medicines)})


PARA = {"drug_name": "Paracetamol", "strength": "500 mg", "form": "tablet", "dose_text": "1 tablet",
        "frequency_text": "twice a day", "food": "after", "duration_days": 3, "as_needed": False, "instructions": None}
SYRUP = {"drug_name": "Cough syrup", "dose_text": "10 ml", "frequency_text": "thrice a day", "food": "any", "as_needed": True}


# ---------------------------------------------------------------------------
# reading the model's medicine list
# ---------------------------------------------------------------------------

def test_the_medicines_the_doctor_said_become_a_draft():
    items, dropped = medication.extract_medications(TURNS, PLAN, chat=reply_with(PARA, SYRUP))
    assert [i["drug_name"] for i in items] == ["Paracetamol", "Cough syrup"] and dropped == []
    para = items[0]
    assert para["strength"] == "500 mg" and para["frequency_text"] == "twice a day" and para["food"] == "after"
    assert para["duration_days"] == 3 and para["from_transcript"] is True and para["dose_times"] == []
    assert items[1]["as_needed"] is True


def test_a_medicine_the_model_invented_is_thrown_away():
    invented = {"drug_name": "Amoxicillin", "strength": "250 mg", "frequency_text": "thrice a day"}
    items, dropped = medication.extract_medications(TURNS, PLAN, chat=reply_with(PARA, invented))
    assert [i["drug_name"] for i in items] == ["Paracetamol"] and dropped == ["Amoxicillin"]


def test_a_medicine_only_the_patient_already_takes_is_still_checked_against_the_words_not_the_speaker():
    """The model is told to skip them; if it does not, only the name's presence is verified by code."""
    items, dropped = medication.extract_medications(TURNS, PLAN, chat=reply_with({"drug_name": "Telma"}))
    assert [i["drug_name"] for i in items] == ["Telma"]          # said in the recording, so it stays a draft line


def test_nothing_prescribed_is_an_empty_draft_not_an_error():
    assert medication.extract_medications(TURNS, PLAN, chat=reply_with()) == ([], [])


def test_odd_values_are_cleaned_not_trusted():
    messy = {"drug_name": " Paracetamol ", "strength": "null", "food": "WITH", "duration_days": "ten", "frequency_text": "N/A"}
    item = medication.extract_medications(TURNS, PLAN, chat=reply_with(messy))[0][0]
    assert item["drug_name"] == "Paracetamol" and item["strength"] is None and item["food"] == "with"
    assert item["duration_days"] is None and item["frequency_text"] is None
    far = medication.extract_medications(TURNS, PLAN, chat=reply_with({**PARA, "duration_days": 400}))[0][0]
    assert far["duration_days"] is None and medication.normalise({"drug_name": "  "}) is None


def test_at_most_twenty_draft_lines():
    many = [{"drug_name": f"Paracetamol {i}"} for i in range(30)]
    assert len(medication.extract_medications(TURNS, PLAN, chat=reply_with(*many))[0]) == 20


@pytest.mark.parametrize("reply", ["", "I cannot help", "{not json", "[1, 2]", '{"medicines": "none"}', '{"other": []}'])
def test_a_reply_that_is_not_a_medicine_list_is_an_error(reply):
    with pytest.raises(medication.MedicationError) as raised:
        medication.extract_medications(TURNS, PLAN, chat=lambda m: reply)
    assert raised.value.code == "medicines_failed"


def test_thinking_text_and_code_fences_around_the_json_are_ignored():
    wrapped = "<think>hmm</think>\n```json\n" + json.dumps({"medicines": [PARA]}) + "\n```"
    assert medication.extract_medications(TURNS, PLAN, chat=lambda m: wrapped)[0][0]["drug_name"] == "Paracetamol"


def test_no_transcript_is_a_clear_error():
    with pytest.raises(medication.MedicationError) as raised:
        medication.extract_medications([], PLAN, chat=reply_with(PARA))
    assert raised.value.code == "no_transcript"


def test_the_model_being_down_is_an_error_with_a_code():
    def down(messages):
        raise summarize.SummaryError("summary_failed", "model down")

    with pytest.raises(medication.MedicationError) as raised:
        medication.extract_medications(TURNS, PLAN, chat=down)
    assert raised.value.code == "medicines_failed"


def test_the_model_is_told_to_use_only_what_was_said():
    seen = {}

    def spy(messages):
        seen["system"] = messages[0]["content"]
        seen["user"] = messages[1]["content"]
        return json.dumps({"medicines": []})

    medication.extract_medications(TURNS, PLAN, chat=spy)
    assert "ONLY" in seen["system"] and "Never guess" in seen["system"]
    assert "Doctor: You have a viral fever" in seen["user"] and PLAN in seen["user"]


# ---------------------------------------------------------------------------
# the doctor agent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,intent", [
    ("what is my schedule today", "schedule"), ("who is next?", "schedule"), ("list my appointments", "schedule"),
    ("what is pending for approval", "pending"), ("anything waiting to sign", "pending"),
    ("any updates?", "updates"), ("what's new", "updates"),
    ("summarise this patient's earlier visits", "history"), ("show history", "history"),
    ("draft the prescription", "draft_prescription"), ("prepare medicines from the recording", "draft_prescription"),
    ("follow up in 2 weeks", "draft_follow_up"), ("schedule a follow-up next week", "draft_follow_up"),
    ("set follow up after 10 days", "draft_follow_up"),
    ("help", "help"),
])
def test_the_doctor_agent_recognises_the_requests(message, intent):
    assert doctor_agent.detect_intent(message) == intent


@pytest.mark.parametrize("message", ["", "tell me a joke", "what is the weather"])
def test_anything_else_is_not_guessed_by_the_rules(message):
    assert doctor_agent.detect_intent(message) is None


@pytest.mark.parametrize("text,days", [
    ("follow up next week", 7), ("in 2 weeks", 14), ("after 10 days", 10), ("in a month", 30), ("in three days", 3),
    ("within 1 week", 7), ("a fortnight", 14), ("in 400 days", None), ("soon", None), ("", None),
])
def test_follow_up_days_are_read_from_time_phrases_only(text, days):
    assert doctor_agent._follow_up_days(text) == days


def test_the_model_classifies_what_the_rules_missed():
    assert doctor_agent.classify_with_model("who do I see after lunch", chat=lambda m: "schedule") == "schedule"
    assert doctor_agent.classify_with_model("x", chat=lambda m: "<think>hmm</think> Pending.") == "pending"
    assert doctor_agent.classify_with_model("x", chat=lambda m: "delete everything") == "help"       # not an intent: no action
    def down(messages):
        raise RuntimeError("model down")
    assert doctor_agent.classify_with_model("x", chat=down) == "help"


class FakeTeamC:
    def __init__(self):
        self.appointments = [
            {"appointment_id": "a1", "appointment_datetime": "2030-03-04T10:30:00+05:30", "patient_label": "Patient ••••1234",
             "appointment_type": "new", "consent_state": "granted", "note_status": "draft", "prescription_status": "draft"},
            {"appointment_id": "a2", "appointment_datetime": "2030-03-04T15:00:00+05:30", "patient_label": "Patient ••••5678",
             "appointment_type": "follow_up", "consent_state": "none", "note_status": None, "prescription_status": None},
            {"appointment_id": "a3", "appointment_datetime": "2030-03-06T09:00:00+05:30", "patient_label": "Patient ••••9999",
             "appointment_type": "new", "consent_state": "none", "note_status": None, "prescription_status": None},
        ]
        self.unread = [{"message_id": "m1", "text": "Cancelled: Patient ••••5678 on Mon 04 Mar at 3:00 PM."}]
        self.read, self.prescriptions, self.follow_ups, self.actions = [], [], [], []
        self.visits = [{"appointment_id": "old", "date": "2030-02-01", "chief_complaint": "Fever", "assessment": "Viral",
                        "plan": "Rest", "medicines": ["Paracetamol 500 mg"]}]
        self.consultation = {"turns": TURNS, "notes": [{"plan": PLAN}]}
        self.error = None

    def _check(self):
        if self.error:
            raise self.error

    def doctor_appointments(self):
        self._check()
        return self.appointments

    def messages(self, unread=True):
        self._check()
        return self.unread

    def mark_messages_read(self, ids=None):
        self.read.extend(ids or [])

    def patient_history(self, appointment_id):
        self._check()
        return self.visits

    def get_consultation(self, consultation_id):
        self._check()
        return self.consultation

    def put_prescription(self, consultation_id, items, source):
        self.prescriptions.append((consultation_id, items, source))
        return {"status": "draft"}

    def put_follow_up(self, consultation_id, day, at):
        self.follow_ups.append((consultation_id, day, at))

    def record_action(self, tool, summary, appointment_id=None, result="ok"):
        self.actions.append((tool, summary))


TODAY = date(2030, 3, 4)


def ask(message, team_c=None, **kwargs):
    team_c = team_c or FakeTeamC()
    kwargs.setdefault("chat", reply_with(PARA, SYRUP))
    return doctor_agent.handle(message, "token", team_c=team_c, today=TODAY, **kwargs), team_c


def test_the_schedule_shows_only_today(  ):
    result, _ = ask("my schedule today")
    assert result["intent"] == "schedule"
    assert "2 appointment(s)" in result["reply"] and "10:30 - Patient ••••1234" in result["reply"]
    assert "15:00 - Patient ••••5678 follow-up" in result["reply"] and "••••9999" not in result["reply"]
    assert "recording granted" in result["reply"] and "note draft" in result["reply"]


def test_an_empty_day_is_said_plainly():
    team_c = FakeTeamC()
    team_c.appointments = []
    assert ask("schedule today", team_c)[0]["reply"] == "You have no appointments today."


def test_pending_lists_only_what_waits_for_the_doctor():
    reply = ask("what is pending")[0]["reply"]
    assert "••••1234: note waiting for your approval and prescription waiting for your signature" in reply
    assert "••••5678" not in reply


def test_updates_are_listed_and_marked_read():
    result, team_c = ask("any updates")
    assert "1 update(s)" in result["reply"] and "Cancelled: Patient ••••5678" in result["reply"]
    assert team_c.read == ["m1"] and result["refresh"] == ["updates"]
    team_c.unread = []
    assert ask("any updates", team_c)[0]["reply"] == "No new updates."


def test_history_is_summarised_from_the_facts_only():
    seen = {}

    def chat(messages):
        seen["facts"] = messages[1]["content"]
        return "One earlier visit for fever."

    result, _ = ask("summarise earlier visits", appointment_id="a1", chat=chat)
    assert result["reply"] == "One earlier visit for fever."
    assert "complaint: Fever" in seen["facts"] and "medicines: Paracetamol 500 mg" in seen["facts"]


def test_history_falls_back_to_a_plain_list_if_the_model_fails():
    def down(messages):
        raise RuntimeError("model down")

    reply = ask("history", appointment_id="a1", chat=down)[0]["reply"]
    assert reply.startswith("Earlier visits:") and "Fever" in reply


def test_history_needs_an_open_appointment_and_may_be_empty():
    assert "Open the appointment first" in ask("history")[0]["reply"]
    team_c = FakeTeamC()
    team_c.visits = []
    assert "no earlier recorded visits" in ask("history", team_c, appointment_id="a1")[0]["reply"]


def test_a_prescription_is_only_drafted_never_signed():
    result, team_c = ask("draft the prescription", consultation_id="c1")
    assert result["refresh"] == ["prescription"] and result["medicines"] == 2
    assert [call[2] for call in team_c.prescriptions] == ["transcript"]
    assert all(item["from_transcript"] for item in team_c.prescriptions[0][1])
    assert "sign" in result["reply"] and "nothing is sent to the patient" in result["reply"]


def test_an_invented_medicine_is_left_out_and_the_doctor_is_told():
    result, team_c = ask("draft the prescription", consultation_id="c1", chat=reply_with(PARA, {"drug_name": "Amoxicillin"}))
    assert [i["drug_name"] for i in team_c.prescriptions[0][1]] == ["Paracetamol"]
    assert "left out Amoxicillin" in result["reply"]


def test_nothing_prescribed_saves_nothing():
    result, team_c = ask("draft the prescription", consultation_id="c1", chat=reply_with())
    assert team_c.prescriptions == [] and "did not find any medicine" in result["reply"]


def test_drafting_needs_a_consultation_and_a_transcript():
    assert "Start the consultation first" in ask("draft the prescription")[0]["reply"]
    team_c = FakeTeamC()
    team_c.consultation = {"turns": [], "notes": []}
    assert "no transcript" in ask("draft the prescription", team_c, consultation_id="c1")[0]["reply"]


def test_the_model_failing_leaves_the_card_to_the_doctor():
    def down(messages):
        raise summarize.SummaryError("summary_failed", "down")

    result, team_c = ask("draft the prescription", consultation_id="c1", chat=down)
    assert team_c.prescriptions == [] and "add them yourself" in result["reply"]


def test_a_follow_up_is_a_draft_date_counted_from_this_visit():
    result, team_c = ask("follow up in 2 weeks", appointment_id="a1", consultation_id="c1")
    assert team_c.follow_ups == [("c1", "2030-03-18", "10:30")]
    assert "Mon 18 Mar at 10:30" in result["reply"] and result["refresh"] == ["followup"]
    assert "approve the note" in result["reply"]


def test_a_follow_up_without_a_time_phrase_asks_when():
    result, team_c = ask("set a follow up", appointment_id="a1", consultation_id="c1")
    assert team_c.follow_ups == [] and "Tell me when" in result["reply"]


def test_unknown_requests_get_the_help_text_and_do_nothing():
    result, team_c = ask("tell me a joke", chat=lambda m: "help")
    assert result["intent"] == "help" and "I only prepare drafts" in result["reply"]
    assert team_c.prescriptions == [] and team_c.follow_ups == []


def test_every_use_is_in_the_audit_trail_without_patient_details():
    _, team_c = ask("any updates")
    ask("draft the prescription", team_c, consultation_id="c1")
    assert team_c.actions == [("assistant_updates", "updates done"), ("assistant_draft_prescription", "draft_prescription done")]


def test_team_c_refusing_is_passed_on_not_hidden():
    team_c = FakeTeamC()
    team_c.error = TeamCError(403, "This is not your appointment")
    with pytest.raises(TeamCError):
        ask("history", team_c, appointment_id="a1")


# ---------------------------------------------------------------------------
# the gateway endpoints
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(doctor_routes.router)
client = TestClient(app)
AUTH = {"Authorization": "Bearer doctor-token"}


def test_the_assistant_needs_a_login_and_a_message():
    assert client.post("/doctor/api/assistant", json={"message": "hi"}).status_code == 401
    assert client.post("/doctor/api/assistant", headers=AUTH, json={"message": "   "}).status_code == 422
    assert client.post("/doctor/api/assistant", headers=AUTH, json={"message": "x" * 501}).status_code == 422


def test_the_assistant_passes_the_doctors_token_and_context_to_the_agent(monkeypatch):
    seen = {}

    def fake(message, token, appointment_id, consultation_id):
        seen.update(message=message, token=token, appointment_id=appointment_id, consultation_id=consultation_id)
        return {"intent": "schedule", "reply": "ok", "refresh": []}

    monkeypatch.setattr(doctor_routes.doctor_agent, "handle", fake)
    response = client.post("/doctor/api/assistant", headers=AUTH, json={"message": "schedule", "appointment_id": "a1", "consultation_id": "c1"})
    assert response.status_code == 200 and response.json()["reply"] == "ok"
    assert seen == {"message": "schedule", "token": "doctor-token", "appointment_id": "a1", "consultation_id": "c1"}


def test_the_assistant_passes_team_c_refusals_on(monkeypatch):
    def refuse(*args):
        raise TeamCError(403, "This is not your appointment")

    monkeypatch.setattr(doctor_routes.doctor_agent, "handle", refuse)
    response = client.post("/doctor/api/assistant", headers=AUTH, json={"message": "history"})
    assert response.status_code == 403 and response.json()["detail"] == "This is not your appointment"


class DraftTeamC:
    def __init__(self, token):
        self.token = token
        self.saved = None

    def get_consultation(self, consultation_id):
        return {"turns": TURNS, "notes": [{"plan": PLAN}]}

    def put_prescription(self, consultation_id, items, source):
        self.saved = (consultation_id, items, source)
        return {"status": "draft", "items": items}


def test_the_draft_endpoint_saves_a_transcript_draft_for_the_doctor(monkeypatch):
    holder = {}
    monkeypatch.setattr(doctor_routes, "TeamC", lambda token: holder.setdefault("t", DraftTeamC(token)))
    monkeypatch.setattr(medication.summarize, "call_chat", reply_with(PARA, {"drug_name": "Amoxicillin"}))
    response = client.post("/doctor/api/consultations/c1/prescription/draft", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["dropped"] == ["Amoxicillin"]
    assert holder["t"].saved[0] == "c1" and holder["t"].saved[2] == "transcript"
    assert holder["t"].token == "doctor-token"


def test_the_draft_endpoint_says_when_no_medicine_was_found(monkeypatch):
    monkeypatch.setattr(doctor_routes, "TeamC", DraftTeamC)
    monkeypatch.setattr(medication.summarize, "call_chat", reply_with())
    response = client.post("/doctor/api/consultations/c1/prescription/draft", headers=AUTH)
    assert response.status_code == 422 and response.json()["detail"] == "no_medicines_found"


def test_the_draft_endpoint_needs_a_login_and_handles_model_failure(monkeypatch):
    assert client.post("/doctor/api/consultations/c1/prescription/draft").status_code == 401
    monkeypatch.setattr(doctor_routes, "TeamC", DraftTeamC)

    def down(messages):
        raise summarize.SummaryError("summary_failed", "down")

    monkeypatch.setattr(medication.summarize, "call_chat", down)
    response = client.post("/doctor/api/consultations/c1/prescription/draft", headers=AUTH)
    assert response.status_code == 502 and response.json()["detail"] == "medicines_failed"
