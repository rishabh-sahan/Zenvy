"""Symptoms -> a short tip -> "it did not improve" -> doctors of the right specialty -> booking, and
"cancel my appointment" said in the middle of a booking.

Team C is the same small in-memory fake the booking tests use (plus doctors of several specialties in two
cities); Redis is a dict; the NLU and the language model are scripted. Nothing here touches the network.
"""
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest
import requests
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
_dummy_key = not os.environ.get("SARVAM_API_KEY")
if _dummy_key:
    os.environ["SARVAM_API_KEY"] = "dummy-key-for-import"

from services.agents import symptom_guide
from services.orchestrator import state_machine
from tests.test_booking_slots import DAY, FakeRedis, IST, _doctor
from tests.test_change_flow import AUTH, FakeTeamCWithAppointments, appointment

if _dummy_key:
    del os.environ["SARVAM_API_KEY"]

NOW = datetime(2030, 3, 4, 8, 0, tzinfo=IST)      # Monday 08:00 in the hospital; DAY is the same Monday
TODAY = date(2030, 3, 4)


class SymptomTeamC(FakeTeamCWithAppointments):
    """Doctors of several specialties in two cities, each with their own free times on DAY."""

    def __init__(self):
        super().__init__()
        self.doctors = [
            _doctor("d1", "Dr. Arjun Rao", "Cardiologist"),
            _doctor("n1", "Dr. Ananya Iyer", "Neurologist", "Royal Mysore Hospital"),
            _doctor("n2", "Dr. Pooja Menon", "Neurologist", "Metro Hospital"),
            _doctor("n3", "Dr. Riya Sharma", "Neurologist", "City Hospital"),
            _doctor("n4", "Dr. Late Doctor", "Neurologist", "Far Hospital"),
            _doctor("g1", "Dr. Aishwarya Das", "General Physician"),
            _doctor("g2", "Dr. Kiran Bengaluru", "General Physician", "Bangalore General", "bangalore"),
            _doctor("p1", "Dr. Child Care", "Pediatrician"),
            _doctor("o1", "Dr. Bone Setter", "Orthopedist", "Bangalore Bones", "bangalore"),
            _doctor("e1", "Dr. Ear Nose", "ENT Specialist"),
            _doctor("s1", "Dr. Skin Care", "Dermatologist"),
        ]
        self.times = {"n1": ["11:00"], "n2": ["09:30"], "n3": ["10:00"], "n4": [], "g1": ["09:45"], "d1": ["09:30", "10:00", "10:30"]}
        self.find_calls = []
        self.actions = []

    def find_doctors(self, query, city=None):
        self.find_calls.append((query, city))
        if self.down:
            raise requests.exceptions.ConnectionError("team c down")
        q = query.lower().replace("dr.", "").strip()
        return [d for d in self.doctors
                if (q in d["name"].lower() or q in d["specialty"].lower()) and (city is None or d["city"] == city)]

    def get_free_slots(self, doctor_id, day, near=None, limit=3):
        if day != DAY:
            return []
        free = [t for t in self.times.get(doctor_id, self.free_times)
                if self.slot_id(doctor_id, day, t) not in self.booked and self.slot_id(doctor_id, day, t) not in self.held]
        if near:
            minutes = lambda t: int(t[:2]) * 60 + int(t[3:5])
            free = sorted(sorted(free, key=lambda t: abs(minutes(t) - minutes(near)))[:limit])
        return [self._slot(doctor_id, day, t) for t in free]

    def record_agent_action(self, *args, **kwargs):
        self.actions.append(args)


class Replies:
    """The scripted language model: what the self-care reply says, and how often it was asked."""

    def __init__(self):
        self.calls = []
        self.reply = "Rest in a quiet room and drink water. See a doctor if it becomes severe or does not improve."

    def __call__(self, text, lang):
        self.calls.append((text, lang))
        return self.reply


@pytest.fixture
def tc(monkeypatch):
    fake = SymptomTeamC()
    model = Replies()
    monkeypatch.setattr(state_machine, "redis_client", FakeRedis())
    monkeypatch.setattr(state_machine, "hospital_today", lambda: TODAY)
    monkeypatch.setattr(state_machine, "_now_ist", lambda: NOW)
    for name in ("find_doctors", "get_free_slots", "hold_slot", "release_slot", "create_appointment", "get_session",
                 "list_my_appointments", "cancel_my_appointment", "reschedule_my_appointment", "record_agent_action"):
        monkeypatch.setattr(state_machine, name, getattr(fake, name))
    monkeypatch.setattr(state_machine, "generate_reply", model)
    monkeypatch.setattr(symptom_guide, "classify_with_model", lambda text, chat=None: None)
    fake.model = model
    return fake


@pytest.fixture
def say(monkeypatch):
    def _say(text, lang="en", session="s1", city="mysore", **fields):
        extracted = {
            "intent": "Unclear", "wants_to_book": False, "doctor_name": None, "appointment_date": None,
            "appointment_time": None, "confirms_booking": None,
        }
        extracted.update(fields)
        monkeypatch.setattr(state_machine, "extract_booking_fields", lambda _text: extracted)
        return state_machine.handle_turn(session, lang, text, city=city)

    return _say


def state(session="s1"):
    return state_machine._get_session_state(session)


def remembered(session="s1"):
    return state_machine._recall_symptom(session)


# ---------------------------------------------------------------------------
# what the table understands
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said,specialty", [
    ("I have a headache", "Neurologist"),
    ("I keep getting migraines and feel dizzy", "Neurologist"),
    ("I have fever and a cough", "General Physician"),
    ("I have fever and a sore throat", "General Physician"),                     # a tie goes to the general physician
    ("my stomach hurts since last night", "General Physician"),
    ("my knee hurts a lot when I climb stairs", "Orthopedist"),
    ("I have a bad backache", "Orthopedist"),
    ("I have a skin rash that itches", "Dermatologist"),
    ("I have a severe ear pain", "ENT Specialist"),
    ("my eyes are red and watery", "Ophthalmologist"),
    ("I missed my period", "Gynecologist"),
    ("I feel anxious and cannot sleep", "Psychiatrist"),
    ("I feel palpitations when I walk", "Cardiologist"),
    ("my son has a fever", "Pediatrician"),
    ("my baby has a rash", "Pediatrician"),
    ("मुझे सिरदर्द है", "Neurologist"),
    ("मुझे बुखार और खांसी है", "General Physician"),
    ("मेरे घुटने में दर्द है", "Orthopedist"),
    ("ನನಗೆ ತಲೆನೋವು ಇದೆ", "Neurologist"),
    ("ನನಗೆ ಜ್ವರ ಮತ್ತು ಕೆಮ್ಮು ಇದೆ", "General Physician"),
    ("ನನಗೆ ಕಿವಿ ನೋವು ಇದೆ", "ENT Specialist"),
])
def test_a_symptom_picks_the_specialty(said, specialty):
    assert symptom_guide.match_symptom(said)["specialty"] == specialty


@pytest.mark.parametrize("said", [
    "I don't have fever",
    "I do not have any chest pain",
    "no fever and no cough",
    "what are the fever ward timings",
    "how much does a headache consultation cost",
    "hello",
    "I want to book an appointment",
    "मुझे बुखार नहीं है",
])
def test_a_denied_symptom_or_a_question_about_the_hospital_is_not_a_symptom(said):
    assert symptom_guide.match_symptom(said) is None


def test_a_grown_up_who_says_their_age_is_not_a_child():
    assert symptom_guide.match_symptom("I am a 30 year old with a headache")["specialty"] == "Neurologist"


@pytest.mark.parametrize("said", [
    "I have chest pain since morning", "I am having difficulty breathing", "I fainted an hour ago",
    "there is heavy bleeding", "he had a seizure", "I want to die",
    "मुझे सीने में दर्द है", "मुझे सांस लेने में तकलीफ है", "ನನಗೆ ಎದೆ ನೋವು ಇದೆ", "ನನಗೆ ಉಸಿರಾಟದ ತೊಂದರೆ ಇದೆ",
])
def test_serious_symptoms_are_flagged(said):
    assert symptom_guide.is_serious(said), said


@pytest.mark.parametrize("said", [
    "I have a mild headache", "I do not have chest pain", "Is chest pain serious?", "no difficulty in breathing",
    "मुझे सीने में दर्द नहीं है", "ನನಗೆ ಎದೆ ನೋವು ಇಲ್ಲ",
])
def test_denials_questions_and_mild_symptoms_are_not_serious(said):
    assert not symptom_guide.is_serious(said), said


@pytest.mark.parametrize("said,kind", [
    ("it did not improve", "not_improved"), ("it didn't help", "not_improved"), ("still hurting", "not_improved"),
    ("no improvement", "not_improved"), ("it got worse", "not_improved"),
    ("which doctor should I see", "which"), ("suggest a doctor for this", "which"), ("who should I see for this", "which"),
    ("I want a doctor", "want"), ("I need to see a doctor", "want"),
    ("ठीक नहीं हुआ", "not_improved"), ("कौन सा डॉक्टर दिखाऊँ", "which"), ("मुझे डॉक्टर चाहिए", "want"),
    ("ಸರಿಹೋಗಿಲ್ಲ", "not_improved"), ("ಯಾವ ವೈದ್ಯರು ನೋಡಬೇಕು", "which"), ("ವೈದ್ಯರು ಬೇಕು", "want"),
    ("hello", None), ("what are the visiting hours", None),
])
def test_asking_for_a_doctor_is_recognised(said, kind):
    assert symptom_guide.doctor_request(said) == kind


# ---------------------------------------------------------------------------
# the model is only the backup, and only for a complaint the table does not know
# ---------------------------------------------------------------------------

def test_the_model_is_asked_only_when_the_table_has_nothing_and_it_sounds_like_a_complaint():
    asked = []

    def chat(messages):
        asked.append(messages[-1]["content"])
        return '{"specialty": "Orthopedist"}'

    assert symptom_guide.classify_with_model("I have a strange pain in my wrist", chat)["specialty"] == "Orthopedist"
    assert symptom_guide.classify_with_model("what time does the hospital open", chat) is None
    assert asked == ["I have a strange pain in my wrist"]                       # the second never reached the model


@pytest.mark.parametrize("reply", ['{"specialty": "Astrologer"}', '{"specialty": "NONE"}', "I think a doctor", "", "{broken"])
def test_an_answer_that_is_not_on_our_list_is_thrown_away(reply):
    assert symptom_guide.classify_with_model("I have a strange pain in my wrist", lambda m: reply) is None


def test_a_model_that_is_down_never_breaks_the_chat():
    def chat(messages):
        raise requests.exceptions.ConnectionError("down")

    assert symptom_guide.classify_with_model("I have a strange pain in my wrist", chat) is None


def test_the_table_wins_over_the_model():
    called = []
    assert symptom_guide.find_symptom("I have a headache", lambda m: called.append(1) or '{"specialty":"Psychiatrist"}')["specialty"] == "Neurologist"
    assert called == []


# ---------------------------------------------------------------------------
# choosing the doctors
# ---------------------------------------------------------------------------

def test_doctors_are_ranked_by_their_earliest_free_time_and_limited_to_three(tc):
    picks = symptom_guide.suggest("Neurologist", "mysore", tc.find_doctors, tc.get_free_slots, NOW)
    assert [d["name"] for d in picks] == ["Dr. Pooja Menon", "Dr. Riya Sharma", "Dr. Ananya Iyer"]     # 09:30, 10:00, 11:00
    assert all(d["any_city"] is False for d in picks)
    assert "Dr. Late Doctor" not in [d["name"] for d in picks]                                         # nothing free: last, and cut


def test_a_doctor_with_no_free_time_is_still_offered_when_there_is_room(tc):
    tc.doctors = [d for d in tc.doctors if d["doctor_id"] in ("n1", "n4")]
    picks = symptom_guide.suggest("Neurologist", "mysore", tc.find_doctors, tc.get_free_slots, NOW)
    assert [d["name"] for d in picks] == ["Dr. Ananya Iyer", "Dr. Late Doctor"] and picks[1]["next_slot"] is None


def test_only_slots_still_ahead_of_now_count(tc):
    late = datetime(2030, 3, 4, 10, 15, tzinfo=IST)
    picks = symptom_guide.suggest("Neurologist", "mysore", tc.find_doctors, tc.get_free_slots, late)
    assert picks[0]["name"] == "Dr. Ananya Iyer" and picks[0]["next_slot"].startswith("2030-03-04T11:00")


def test_a_city_without_that_specialty_falls_back_to_every_city(tc):
    picks = symptom_guide.suggest("Neurologist", "bangalore", tc.find_doctors, tc.get_free_slots, NOW)
    assert len(picks) == 3 and all(d["any_city"] for d in picks)
    assert tc.find_calls == [("Neurologist", "bangalore"), ("Neurologist", None)]


def test_no_city_means_every_city_without_claiming_a_fallback(tc):
    picks = symptom_guide.suggest("Orthopedist", None, tc.find_doctors, tc.get_free_slots, NOW)
    assert [d["name"] for d in picks] == ["Dr. Bone Setter"] and picks[0]["any_city"] is False


def test_a_doctor_search_that_returns_another_specialty_is_ignored(tc):
    tc.find_doctors = lambda query, city=None: [_doctor("x", "Dr. Wrong", "Dermatologist")]
    assert symptom_guide.suggest("Neurologist", "mysore", tc.find_doctors, tc.get_free_slots, NOW) == []


# ---------------------------------------------------------------------------
# the conversation
# ---------------------------------------------------------------------------

def test_a_symptom_gets_the_tip_and_is_remembered_but_no_doctors_yet(tc, say):
    reply = say("I have a headache")
    assert reply == tc.model.reply and len(tc.model.calls) == 1
    assert remembered() == {"specialty": "Neurologist", "symptom": "headache or dizziness", "serious": False}
    assert state() is None and tc.find_calls == []


def test_it_did_not_improve_suggests_the_doctors(tc, say):
    say("I have a headache")
    reply = say("it did not improve")
    assert reply.startswith("For this, I suggest seeing a Neurologist.")
    first, second, third = (reply.index(n) for n in ("Pooja Menon", "Riya Sharma", "Ananya Iyer"))
    assert first < second < third
    assert "1) Dr. Pooja Menon, Metro Hospital, Mysuru (next free Mon 04 Mar at 09:30)" in reply
    entry = state()
    assert entry["state"] == "ASK_DOCTOR" and entry["from_symptom"] and len(entry["candidates"]) == 3
    assert tc.find_calls[0] == ("Neurologist", "mysore")
    assert tc.actions and tc.actions[0][1] == "suggest_doctors" and "Neurologist: 3" in tc.actions[0][2]
    assert "Dr. Late Doctor" not in reply and len(tc.model.calls) == 1             # no second tip


def test_the_whole_way_from_a_headache_to_a_booked_appointment(tc, say):
    say("I have a headache")
    say("it did not improve")
    assert "What date" in say("book the first one")
    assert state()["state"] == "ASK_DATE" and state()["doctor"]["name"] == "Dr. Pooja Menon"
    assert "what time" in say("Monday", appointment_date=DAY).lower()
    assert "confirm" in say("9:30", appointment_time="09:30").lower()
    assert "confirmed" in say("yes", confirms_booking=True)
    assert [a["slot_id"] for a in tc.appointments] == [f"n2|{DAY}|09:30"]            # the doctor the patient chose
    assert state() is None


@pytest.mark.parametrize("answer,expected", [
    ("the second one", "Dr. Riya Sharma"), ("number 3", "Dr. Ananya Iyer"), ("Dr. Riya Sharma please", "Dr. Riya Sharma"),
    ("the one at Royal Mysore Hospital", "Dr. Ananya Iyer"),
])
def test_the_patient_can_pick_by_number_name_or_hospital(tc, say, answer, expected):
    say("I have a headache")
    say("which doctor should I see")
    say(answer)
    assert state()["doctor"]["name"] == expected


def test_an_unclear_answer_asks_again_with_the_same_doctors(tc, say):
    say("I have a headache")
    say("it did not improve")
    reply = say("hmm what about the weather")
    assert reply.startswith("Which of these doctors would you like to book?") and "Pooja Menon" in reply
    assert state()["state"] == "ASK_DOCTOR" and len(state()["candidates"]) == 3


def test_no_thanks_drops_the_suggestion_and_leaves_nothing_held(tc, say):
    say("I have a headache")
    say("it did not improve")
    assert "No problem" in say("no thanks")
    assert state() is None and tc.held == {}


def test_a_symptom_and_a_request_in_one_sentence(tc, say):
    reply = say("I have a knee problem, which doctor should I see", city="bangalore")
    assert reply.startswith("For this, I suggest seeing a Orthopedist.") and "Dr. Bone Setter" in reply
    assert tc.model.calls == []                                                    # no tip: they asked for a doctor


def test_serious_symptoms_get_urgent_advice_first_and_doctors_at_once(tc, say):
    reply = say("I have chest pain since morning")
    assert reply.startswith("This may need urgent care. Please call 112 or go to the nearest emergency department now.")
    assert "Cardiologist" in reply and "Dr. Arjun Rao" in reply
    assert tc.model.calls == []                                                    # no self-care tip for chest pain
    assert state()["from_symptom"] and remembered()["serious"] is True


def test_serious_wording_in_a_booking_request_still_gets_the_urgent_advice(tc, say):
    reply = say("I have chest pain, please book me a doctor", wants_to_book=True)
    assert reply.startswith("This may need urgent care.")


def test_it_did_not_improve_after_a_serious_symptom_repeats_the_urgent_advice(tc, say):
    say("I fainted this morning")
    state_machine._delete_session_state("s1")                                      # they ignored the list
    assert say("it did not improve").startswith("This may need urgent care.")


def test_no_symptom_known_means_it_asks_what_the_problem_is(tc, say):
    assert "Tell me what problem you are having" in say("which doctor should I see")
    assert "Tell me what problem you are having" in say("it did not improve", session="s2")
    assert state() is None


def test_i_want_a_doctor_with_no_symptom_is_an_ordinary_booking(tc, say):
    reply = say("I want a doctor", wants_to_book=True)
    assert "Which doctor or department" in reply and state()["state"] == "ASK_DOCTOR" and not state().get("from_symptom")


def test_i_want_a_doctor_after_a_symptom_suggests_the_right_kind(tc, say):
    say("I have a skin rash that itches")
    assert "Dr. Skin Care" in say("I want a doctor", wants_to_book=True)


def test_a_medicine_question_is_not_taken_for_a_symptom(tc, say, monkeypatch):
    monkeypatch.setattr(state_machine.patient_agent, "handle", lambda auth, lang, intent: f"[medicine:{intent}]")
    assert say("I took my tablet for fever") == "[medicine:taken]"
    assert remembered() is None and tc.model.calls == []


def test_a_booking_request_that_only_describes_a_symptom_suggests_the_right_doctors(tc, say):
    """The patient did not name a doctor or a specialty: "Neurologist" was the model's own guess from the symptom."""
    reply = say("book an appointment, I have a headache", wants_to_book=True, doctor_name="Neurologist")
    assert reply.startswith("For this, I suggest seeing a Neurologist.") and tc.model.calls == []


def test_a_booking_request_that_names_the_specialty_stays_a_booking(tc, say):
    reply = say("book an appointment with a neurologist, I have a headache", wants_to_book=True, doctor_name="Neurologist")
    assert not reply.startswith("For this, I suggest") and tc.model.calls == []


def test_an_ordinary_hospital_question_is_unchanged(tc, say):
    assert say("what are the visiting hours") == tc.model.reply
    assert remembered() is None and state() is None


def test_a_self_care_reply_that_names_a_medicine_is_replaced_by_the_safe_wording(tc, say):
    tc.model.reply = "You could take paracetamol 500 mg and rest."
    reply = say("I have a headache")
    assert "paracetamol" not in reply.lower() and "see a doctor" in reply.lower()


def test_a_diagnosis_in_the_self_care_reply_is_replaced_too(tc, say):
    tc.model.reply = "This sounds like a migraine. The diagnosis is clear."
    assert "diagnosis" not in say("I have a headache").lower()


def test_no_doctor_of_that_specialty_says_so(tc, say):
    tc.doctors = [d for d in tc.doctors if d["specialty"] != "Neurologist"]
    say("I have a headache")
    assert "couldn't find a Neurologist" in say("it did not improve") and state() is None


def test_a_team_c_that_is_down_gives_the_usual_apology_and_no_stuck_state(tc, say):
    say("I have a headache")
    tc.down = True
    assert "couldn't complete" in say("it did not improve") and state() is None


def test_a_city_with_none_of_that_specialty_says_the_doctors_are_in_other_cities(tc, say):
    say("I have a headache", city="bangalore")
    reply = say("it did not improve", city="bangalore")
    assert "in other cities" in reply and "Pooja Menon" in reply


def test_the_page_may_spell_the_city_the_way_the_patient_says_it(tc, say):
    say("I have a headache", city="Mysuru")
    say("it did not improve", city="Mysuru")
    assert tc.find_calls[0] == ("Neurologist", "mysore")


def test_no_city_at_all_searches_every_city(tc, say):
    say("I have a headache", city=None)
    reply = say("it did not improve", city=None)
    assert tc.find_calls[0] == ("Neurologist", None) and "Pooja Menon" in reply and "in other cities" not in reply


def test_the_symptom_is_forgotten_with_the_chat_not_shared_between_chats(tc, say):
    say("I have a headache", session="a")
    assert "Tell me what problem" in say("it did not improve", session="b")


# ---------------------------------------------------------------------------
# Hindi and Kannada
# ---------------------------------------------------------------------------

def test_a_hindi_conversation_from_symptom_to_doctors(tc, say):
    tc.model.reply = "आराम करें और पानी पिएँ। अगर यह बढ़े तो डॉक्टर को दिखाइए।"
    assert say("मुझे सिरदर्द है", lang="hi") == tc.model.reply
    reply = say("ठीक नहीं हुआ", lang="hi")
    assert "इसके लिए मैं Neurologist से मिलने की सलाह देता हूँ" in reply and "Pooja Menon" in reply and "अगला खाली समय" in reply
    assert tc.model.calls == [("मुझे सिरदर्द है", "hi")]


def test_a_kannada_conversation_from_symptom_to_doctors(tc, say):
    say("ನನಗೆ ತಲೆನೋವು ಇದೆ", lang="kn")
    reply = say("ಸರಿಹೋಗಿಲ್ಲ", lang="kn")
    assert "ಇದಕ್ಕಾಗಿ Neurologist" in reply and "ಮುಂದಿನ ಖಾಲಿ ಸಮಯ" in reply and "Pooja Menon" in reply


def test_serious_symptoms_in_hindi_and_kannada_get_the_urgent_advice_in_that_language(tc, say):
    assert say("मुझे सीने में दर्द है", lang="hi").startswith("इसमें तुरंत इलाज की ज़रूरत हो सकती है।")
    assert say("ನನಗೆ ಎದೆ ನೋವು ಇದೆ", lang="kn", session="s2").startswith("ಇದಕ್ಕೆ ತುರ್ತು ಚಿಕಿತ್ಸೆ ಬೇಕಾಗಬಹುದು.")


def test_hindi_asking_for_a_doctor_without_a_symptom(tc, say):
    assert "बताइए आपको क्या समस्या है" in say("कौन सा डॉक्टर दिखाऊँ", lang="hi")


# ---------------------------------------------------------------------------
# "cancel my appointment" in the middle of a booking
# ---------------------------------------------------------------------------

def start_booking(say, to="CONFIRM"):
    """A booking in progress with Dr. Arjun Rao: asking for the date, the time, or waiting for the yes."""
    say("I want to book Dr. Arjun Rao", wants_to_book=True, doctor_name="Arjun Rao")
    if to == "ASK_DATE":
        return
    say("Monday at 10", appointment_date=DAY, appointment_time="10:00") if to == "CONFIRM" else say("Monday", appointment_date=DAY)


@pytest.mark.parametrize("stage", ["ASK_DATE", "ASK_TIME", "CONFIRM"])
def test_cancel_my_appointment_mid_booking_asks_which_one_they_mean(tc, say, stage):
    start_booking(say, stage)
    assert state()["state"] == stage
    reply = say("I need to cancel my scheduled appointment")
    assert "stop this booking, or cancel an appointment you already have" in reply
    entry = state()
    assert entry["state"] == "CANCEL_SCOPE" and entry["booking"]["state"] == stage      # the booking is kept aside, not lost
    assert tc.cancel_calls == []


@pytest.mark.parametrize("answer", ["1", "this booking", "stop this one", "never mind", "the new booking", "first"])
def test_stopping_this_booking_releases_its_slot_and_touches_no_appointment(tc, say, answer):
    start_booking(say)
    assert tc.held                                                                  # the slot is held for the patient
    say("cancel my appointment")
    reply = say(answer)
    assert "cancelled that booking request" in reply
    assert tc.held == {} and tc.cancel_calls == [] and state() is None
    assert [a["status"] for a in tc.appointments_of_patient] == ["confirmed"]


@pytest.mark.parametrize("answer", ["2", "the appointment I already have", "my existing appointment", "the one already booked"])
def test_cancelling_the_existing_appointment_drops_the_booking_and_follows_the_cancel_flow(tc, say, answer):
    start_booking(say)
    say("cancel my appointment")
    reply = say(answer)
    assert "Do you want to cancel your appointment with Dr. Arjun Rao on Mon 04 Mar at 10:00" in reply
    assert tc.held == {}                                                            # the unfinished booking let go of its slot
    assert state()["state"] == "CANCEL_CONFIRM" and tc.cancel_calls == []
    assert "Done" in say("yes") and tc.cancel_calls == [("apt-1", AUTH)] and state() is None
    assert tc.appointments == []                                                   # and nothing new was booked


def test_the_patient_can_say_no_to_the_cancellation_and_keep_the_appointment(tc, say):
    start_booking(say)
    say("cancel my appointment")
    say("2")
    assert "kept" in say("no") and tc.cancel_calls == [] and state() is None and tc.held == {}


def test_with_several_appointments_it_asks_which_one(tc, say):
    tc.appointments_of_patient = [appointment("apt-1"), appointment("apt-2", "Dr. Priya Sharma", "d2", "2030-03-05T11:00:00+05:30")]
    start_booking(say)
    say("cancel my appointment")
    reply = say("2")
    assert "Which appointment would you like to cancel?" in reply and "Priya Sharma" in reply
    say("the second")
    assert "Done" in say("yes")
    assert tc.cancel_calls == [("apt-2", AUTH)]


def test_an_unclear_answer_repeats_the_question_and_changes_nothing(tc, say):
    start_booking(say)
    say("cancel my appointment")
    assert "stop this booking" in say("hmm what do you mean")
    assert state()["state"] == "CANCEL_SCOPE" and tc.held and tc.cancel_calls == []


def test_the_patient_can_carry_on_with_the_booking(tc, say):
    start_booking(say)
    say("cancel my appointment")
    reply = say("actually continue with the booking")
    assert "confirm" in reply.lower() and state()["state"] == "CONFIRM"
    assert "confirmed" in say("yes", confirms_booking=True) and len(tc.appointments) == 1


def test_with_nothing_booked_it_simply_stops_the_booking(tc, say):
    tc.appointments_of_patient = []
    start_booking(say)
    reply = say("cancel my appointment")
    assert "cancelled that booking request" in reply and state() is None and tc.held == {}


def test_not_logged_in_it_simply_stops_the_booking(tc, say):
    tc.user_id = None
    start_booking(say)
    assert "cancelled that booking request" in say("cancel my appointment") and state() is None


def test_if_the_appointment_cannot_be_changed_the_booking_is_left_alone(tc, say):
    tc.appointments_of_patient = [appointment("apt-1", can_change=False)]
    start_booking(say)
    reply = say("cancel my appointment")
    assert "cancelled that booking request" in reply                              # nothing cancellable: it is the booking


def test_team_c_failing_to_list_appointments_keeps_the_booking_going(tc, say):
    start_booking(say)
    tc.crash = True
    reply = say("cancel my appointment")
    assert "couldn't change the appointment" in reply
    tc.crash = False
    assert state()["state"] == "CONFIRM" and tc.held                               # still waiting for the yes


@pytest.mark.parametrize("bare", ["cancel", "stop", "never mind", "forget it"])
def test_a_bare_cancel_or_never_mind_still_just_stops_the_booking(tc, say, bare):
    start_booking(say, "ASK_DATE")
    reply = say(bare)
    assert "cancelled that booking request" in reply and state() is None


def test_cancel_it_at_the_confirmation_step_still_declines_the_booking(tc, say):
    start_booking(say)
    assert "cancelled that booking request" in say("no, cancel it") and tc.held == {} and state() is None


def test_do_not_cancel_my_appointment_does_not_ask_the_which_one_question(tc, say):
    start_booking(say, "ASK_DATE")
    assert "stop this booking, or cancel" not in say("don't cancel my appointment")
    assert tc.cancel_calls == [] and [a["status"] for a in tc.appointments_of_patient] == ["confirmed"]


def test_a_reschedule_in_progress_is_not_interrupted_by_the_new_question(tc, say):
    say("I want to reschedule my appointment")
    assert state()["flow"] == "reschedule"
    say("cancel my appointment")
    assert state() is None or state()["state"] != "CANCEL_SCOPE"


def test_the_question_and_the_answers_in_hindi(tc, say):
    start_booking(say)
    reply = say("मेरी अपॉइंटमेंट रद्द करो", lang="hi")
    assert "क्या आप यह बुकिंग रोकना चाहते हैं" in reply
    assert "रद्द करना चाहते हैं" in say("2", lang="hi") and state()["state"] == "CANCEL_CONFIRM"


def test_the_question_in_kannada(tc, say):
    start_booking(say)
    assert "ಈ ಬುಕಿಂಗ್ ಅನ್ನು ನಿಲ್ಲಿಸಬೇಕೇ" in say("ನನ್ನ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ರದ್ದುಗೊಳಿಸಿ", lang="kn")


def test_a_symptom_flow_can_be_cancelled_mid_booking_too(tc, say):
    say("I have a headache")
    say("it did not improve")
    say("book the first one")                                   # a booking with Dr. Pooja Menon is now in progress
    assert "stop this booking" in say("cancel my appointment")
    assert "cancelled that booking request" in say("1") and state() is None


# ---------------------------------------------------------------------------
# the page's city reaches the conversation
# ---------------------------------------------------------------------------

def test_the_gateway_passes_the_city_to_the_conversation(monkeypatch):
    from fastapi.testclient import TestClient

    if _dummy_key:
        os.environ["SARVAM_API_KEY"] = "dummy-key-for-import"
    from services.gateway import main as gateway
    seen = []
    monkeypatch.setattr(gateway, "_get_or_create_session", lambda *a, **k: "s-1")
    monkeypatch.setattr(gateway, "handle_turn", lambda session, lang, text, city=None: seen.append(city) or "ok")
    monkeypatch.setattr(gateway, "add_turn", lambda *a, **k: None)
    client = TestClient(gateway.app)

    assert client.post("/channels/web/ask", data={"text": "hello", "city": "mysore"}).status_code == 200
    assert client.post("/channels/web/ask", data={"text": "hello"}).status_code == 200
    assert client.post("/channels/web/message", json={"text": "hello", "city": "bangalore"}).status_code == 200
    assert seen == ["mysore", None, "bangalore"]


# ---------------------------------------------------------------------------
# a complaint no specialty of ours fits: start with a General Physician
# ---------------------------------------------------------------------------

def test_a_complaint_nothing_can_place_is_remembered_as_a_general_physician(tc, say):
    reply = say("my gums bleed when I brush my teeth")
    assert reply == tc.model.reply                                             # the ordinary reply, no special tip
    assert remembered()["specialty"] == "General Physician"
    assert "Dr. Aishwarya Das" in say("which doctor should I see")


def test_a_complaint_and_a_request_together_use_the_fallback(tc, say):
    assert "General Physician" in say("I have a lump under my arm, which doctor should I see")


def test_a_denied_complaint_does_not_start_a_fallback(tc, say):
    say("I have no pain and nothing hurts")
    assert remembered() is None


def test_the_fallback_does_not_replace_a_clearer_symptom(tc, say):
    say("I have a headache")
    say("my gums bleed when I brush my teeth")
    assert remembered()["specialty"] == "General Physician"            # the newest complaint is the one that counts
    say("I have a headache again")
    assert remembered()["specialty"] == "Neurologist"


# ---------------------------------------------------------------------------
# found by the first real use on the patient page: the patient's own words
# ---------------------------------------------------------------------------

CHILD = "My child has fever from last two to three days with cough. What I need to do now?"
SKIN = "From yesterday I have skin irritation like like my skin is feeling like I have burning sensation"


def test_the_first_real_conversation_a_child_with_a_fever_then_suggest_me_the_doctors(tc, say):
    say(CHILD)
    reply = say("Suggest me the doctors to book the appointment", wants_to_book=True, intent="Book Appointment")
    assert reply.startswith("For this, I suggest seeing a Pediatrician.") and "Dr. Child Care" in reply
    assert state()["from_symptom"] and state()["state"] == "ASK_DOCTOR"


def test_the_second_real_conversation_a_symptom_the_model_took_for_a_booking(tc, say):
    """The language model said "wants to book" for a plain symptom sentence: it must still get the tip."""
    reply = say(SKIN, wants_to_book=True, intent="Book Appointment")
    assert reply == tc.model.reply and remembered()["specialty"] == "Dermatologist"
    assert state() is None
    assert "Dr. Skin Care" in say("suggest me the doctors")


def test_a_symptom_with_book_me_a_doctor_suggests_doctors_at_once(tc, say):
    reply = say("I have a skin irritation, please book me a doctor", wants_to_book=True)
    assert reply.startswith("For this, I suggest seeing a Dermatologist.") and tc.model.calls == []


def test_book_an_appointment_right_after_a_symptom_suggests_the_right_doctors(tc, say):
    say("I have a headache")
    assert "Neurologist" in say("I want to book an appointment", wants_to_book=True)


def test_a_named_specialty_is_still_an_ordinary_booking_even_after_a_symptom(tc, say):
    say("I have a headache")
    reply = say("book an appointment with a cardiologist", wants_to_book=True)               # the model missed doctor_name
    assert "Neurologist" not in reply and state()["state"] == "ASK_DOCTOR" and not state().get("from_symptom")


def test_a_named_doctor_is_never_hijacked(tc, say):
    say("I have a headache")
    say("I want to book Dr. Arjun Rao", wants_to_book=True, doctor_name="Arjun Rao")
    assert state()["doctor"]["name"] == "Dr. Arjun Rao"


@pytest.mark.parametrize("said", [
    "Suggest me the doctors to book the appointment", "suggest me the doctors", "can you suggest a doctor",
    "please recommend a good doctor for this", "which doctors are available", "which doctor should I consult",
    "who can I see for this", "what kind of doctor should I see", "suggest the right doctor",
])
def test_the_ways_of_asking_us_to_choose_the_doctor(said):
    assert symptom_guide.doctor_request(said) == "which"


@pytest.mark.parametrize("said", [
    "show me the doctors", "show me doctors for this", "I need a doctor", "I need a doctor for my child", "find me a doctor",
    "list the doctors", "give me the doctor options", "doctors available", "book a doctor", "looking for a doctor",
    "tell me the doctors names", "I want to see a doctor",
])
def test_the_ways_of_asking_to_see_doctors(said):
    assert symptom_guide.doctor_request(said) == "want"


@pytest.mark.parametrize("said", [
    "I want to book an appointment", "get me a doctor appointment", "what are the doctors timings", "hello there",
    "my doctor appointment is tomorrow",
])
def test_ordinary_sentences_are_not_taken_for_doctor_requests(said):
    assert symptom_guide.doctor_request(said) is None


@pytest.mark.parametrize("said", ["I want to book an appointment", "book me a slot", "schedule a visit", "ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಾಡಿ", "अपॉइंटमेंट बुक करो"])
def test_booking_words_are_recognised(said):
    assert symptom_guide.explicit_booking(said)


@pytest.mark.parametrize("said", [CHILD, SKIN, "I have a headache", "मुझे सिरदर्द है"])
def test_a_plain_symptom_has_no_booking_word(said):
    assert not symptom_guide.explicit_booking(said)


# ---------------------------------------------------------------------------
# found by running 20 natural sentences against the real stack
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said,specialty", [
    ("my daughter is not eating and has loose motions", "Pediatrician"),            # "not" must not reach across "and"
    ("cough and cold since 4 days", "General Physician"),                           # no "I" or "my"
    ("fever since yesterday", "General Physician"),
    ("stomach pain and vomiting from morning", "General Physician"),
    ("I have a headache but no fever", "Neurologist"),
    ("I am not sleeping well and feel anxious", "Psychiatrist"),
    ("severe back pain for 3 days", "Orthopedist"),
    ("burning sensation in my eyes", "Ophthalmologist"),
])
def test_natural_sentences_without_a_pronoun_or_with_a_not_in_front(said, specialty):
    assert symptom_guide.match_symptom(said)["specialty"] == specialty


@pytest.mark.parametrize("said", [
    "what is a fever", "is a headache dangerous?", "what are the fever ward timings", "how much is the cough clinic fee",
    "no fever and no cough", "I do not have a cough and I have no fever", "without any pain",
])
def test_questions_and_denials_are_still_not_symptoms(said):
    assert symptom_guide.match_symptom(said) is None


def test_a_denied_symptom_followed_by_a_real_one():
    assert symptom_guide.match_symptom("no fever but I have a bad headache")["specialty"] == "Neurologist"


def test_a_doctor_name_the_model_only_guessed_from_the_symptom_is_not_a_named_doctor(tc, say):
    """Found live: "skin irritation ... book me a doctor" came back with doctor_name "Dermatologist"."""
    reply = say("I have skin irritation with burning sensation, please book me a doctor", wants_to_book=True, doctor_name="Dermatologist")
    assert reply.startswith("For this, I suggest seeing a Dermatologist.") and "Dr. Skin Care" in reply


def test_a_doctor_the_patient_really_named_still_wins(tc, say):
    say("I have a headache")
    say("please book Dr. Arjun Rao for me", wants_to_book=True, doctor_name="Arjun Rao")
    assert state()["doctor"]["name"] == "Dr. Arjun Rao" and not state().get("from_symptom")


@pytest.mark.parametrize("said,guess,named", [
    ("please book me a doctor", "Dermatologist", False),
    ("book an appointment with Arjun Rao", "Arjun Rao", True),
    ("book a cardiologist", "Cardiologist", True),
    ("I want to book an appointment", None, False),
    ("डॉक्टर अर्जुन राव से बुक करो", "Arjun Rao", True),
])
def test_naming_a_doctor_means_the_patient_said_it(said, guess, named):
    assert symptom_guide.names_a_doctor(said, guess) is named
