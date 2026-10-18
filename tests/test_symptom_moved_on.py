"""The patient moves on while an old booking question is still open.

Found by the second real use of the patient page: an earlier "which doctor?" left a booking open (Redis keeps it for
an hour), and every new sentence, even "my hairfall has been increased from past 2-3 months", was answered with the
old "I found more than one match: 1) Dr. Rahul Gowda (Pediatrician...)". A new symptom, or a request to choose a doctor,
now closes the unfinished booking (its held slot goes back) and is answered as what it is.
"""
import pytest

from services.agents import symptom_guide
from tests.test_booking_slots import DAY
from tests.test_symptom_flow import SymptomTeamC, remembered, say, start_booking, state, tc  # noqa: F401  (fixtures)

HAIRFALL = "my hairfall has been increased from past 2-3 months"
HAND = "i have pain in my left hand from this morning"


def leave_a_booking_open(say):
    """What the patient's chat looked like: a doctor list from an earlier question, still waiting for an answer."""
    say("my son has a fever")
    say("suggest me the doctors")
    assert state()["state"] == "ASK_DOCTOR" and state()["from_symptom"]


def test_a_new_symptom_is_not_swallowed_by_the_old_doctor_list(tc, say):
    leave_a_booking_open(say)
    reply = say(HAIRFALL)
    assert "more than one match" not in reply and "Which of these doctors" not in reply
    assert reply == tc.model.reply                                     # the usual short tip
    assert remembered()["specialty"] == "Dermatologist" and state() is None


def test_the_next_request_then_suggests_the_doctors_for_the_new_symptom(tc, say):
    leave_a_booking_open(say)
    say(HAIRFALL)
    reply = say("Suggest me the doctors to book the appointment", wants_to_book=True)
    assert reply.startswith("For this, I suggest seeing a Dermatologist.") and "Dr. Skin Care" in reply
    assert "Dr. Child Care" not in reply


def test_hand_pain_after_an_old_doctor_list(tc, say):
    leave_a_booking_open(say)
    reply = say(HAND)
    assert reply == tc.model.reply and remembered()["specialty"] == "Orthopedist" and state() is None


def test_an_old_booking_that_had_held_a_slot_lets_it_go(tc, say):
    start_booking(say)                                                 # Dr. Arjun Rao, waiting for the yes
    assert tc.held
    say("I have a headache since yesterday")
    assert tc.held == {} and state() is None and remembered()["specialty"] == "Neurologist"


def test_which_doctor_should_i_see_replaces_the_old_list_with_the_right_one(tc, say):
    leave_a_booking_open(say)
    say("I have a skin rash that itches")                              # no booking word: a tip, and the old list goes
    assert state() is None
    reply = say("which doctor should I see")
    assert "Dr. Skin Care" in reply and "Dr. Child Care" not in reply


@pytest.mark.parametrize("said", ["which doctor should I see", "it did not improve", "it got worse"])
def test_asking_again_for_a_doctor_while_an_old_list_is_open_gives_a_fresh_suggestion(tc, say, said):
    leave_a_booking_open(say)
    reply = say(said)
    assert reply.startswith("For this, I suggest seeing a Pediatrician.") and "Which of these doctors" not in reply
    assert state()["from_symptom"] and state()["state"] == "ASK_DOCTOR"


def test_a_real_answer_to_the_old_question_still_works(tc, say):
    leave_a_booking_open(say)
    say("the first one")
    assert state()["state"] == "ASK_DATE" and state()["doctor"]["specialty"] == "Pediatrician"


def test_a_date_answer_is_not_a_new_topic_even_if_it_mentions_a_day(tc, say):
    start_booking(say, "ASK_DATE")
    assert "what time" in say("Monday", appointment_date=DAY).lower()


def test_naming_a_doctor_with_a_symptom_in_the_same_sentence_stays_a_booking(tc, say):
    start_booking(say, "ASK_DATE")
    reply = say("my knee hurts, I want Dr. Arjun Rao", wants_to_book=True, doctor_name="Arjun Rao")
    assert "For this, I suggest" not in reply and state() is not None


def test_a_reschedule_in_progress_is_not_interrupted_by_a_symptom(tc, say):
    say("I want to reschedule my appointment")
    assert state()["flow"] == "reschedule"
    say("I have a headache since morning")
    assert state() is not None and state()["flow"] == "reschedule"


@pytest.mark.parametrize("said,specialty", [
    (HAIRFALL, "Dermatologist"),
    ("hair fall is increasing", "Dermatologist"),
    ("my hair is falling a lot", "Dermatologist"),
    (HAND, "Orthopedist"),
    ("pain in my right leg since last night", "Orthopedist"),
    ("my wrist is swollen", "Orthopedist"),
    ("neck pain for a week", "Orthopedist"),
    ("मेरे हाथ में दर्द है", "Orthopedist"),
    ("ನನಗೆ ಕೈ ನೋವು ಇದೆ", "Orthopedist"),
])
def test_more_everyday_phrasings(said, specialty):
    assert symptom_guide.match_symptom(said)["specialty"] == specialty


@pytest.mark.parametrize("said", ["skin doctor", "I need a skin doctor", "knee doctor please"])
def test_a_kind_of_doctor_chosen_by_its_symptom_is_a_doctor_request_not_a_tip(tc, say, said):
    reply = say(said)
    assert ("suggest seeing a" in reply or "in other cities" in reply) and tc.model.calls == []


@pytest.mark.parametrize("said", ["number 3", "number two", "the number one please", "my number is 5", "a number of times"])
def test_the_word_number_is_not_a_symptom(said):
    """Found by a test: the table's "numb..." pattern matched "number", so "number 3" (picking a doctor) was a symptom."""
    assert symptom_guide.match_symptom(said) is None


@pytest.mark.parametrize("said", ["I will come back tomorrow", "pain is gone, I came back from the clinic", "on the other hand it is fine"])
def test_everyday_words_next_to_a_body_part_are_not_symptoms(said):
    assert symptom_guide.match_symptom(said) is None


@pytest.mark.parametrize("said,specialty", [
    ("dermatologist", "Dermatologist"), ("Neurologist", "Neurologist"), ("ENT", "ENT Specialist"), ("eye specialist", "Ophthalmologist"),
    ("heart doctor", "Cardiologist"), ("orthopedic", "Orthopedist"), ("paediatrician please", "Pediatrician"),
    ("I need a gynecologist", "Gynecologist"), ("psychiatrist", "Psychiatrist"), ("general physician", "General Physician"),
])
def test_a_kind_of_doctor_said_on_its_own_lists_those_doctors(tc, say, said, specialty):
    """The page greets with "Which type of doctor are you looking for?": the answer must list doctors."""
    reply = say(said)
    assert specialty in reply and "Tell me what problem" not in reply          # doctors, "none in your city", or "none found"
    assert tc.model.calls == []                                                # and never a self-care tip


@pytest.mark.parametrize("said", [
    "what does a dermatologist do", "which dermatologist is best?", "book an appointment with a dermatologist",
    "I want to book a cardiologist appointment", "dermatologist timings and fees and the location of the clinic please",
])
def test_questions_and_booking_requests_naming_a_specialty_are_left_to_the_other_paths(tc, say, said):
    assert symptom_guide.specialty_named(said) is None


def test_asking_about_the_hospital_is_not_a_symptom_even_with_a_doctor_word(tc, say):
    assert say("what are the skin doctor timings") == tc.model.reply
    assert remembered() is None


@pytest.mark.parametrize("specialty,article", [
    ("Neurologist", "a Neurologist"), ("Orthopedist", "an Orthopedist"), ("ENT Specialist", "an ENT Specialist"),
    ("Ophthalmologist", "an Ophthalmologist"), ("General Physician", "a General Physician"), ("Pediatrician", "a Pediatrician"),
])
def test_the_english_wording_uses_a_or_an(specialty, article):
    from services.orchestrator.templates import render_template
    assert f"seeing {article}." in render_template("DOCTOR_SUGGEST", "en", specialty=specialty, options="1) x")
    assert f"couldn't find {article} right now" in render_template("SYMPTOM_NO_DOCTOR", "en", specialty=specialty)
