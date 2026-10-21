"""Live medicines: what the doctor says during the visit becomes medicine rows, with morning / noon / night.

The model reads the conversation; the code checks it. These tests use a fake model and prove that the checks hold:
nothing is invented, nothing the doctor did not say is filled in, and a slip of the model never removes a medicine.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.gateway import doctor_routes
from services.scribe import live, live_medicines as lm, summarize
from services.scribe.team_c import TeamCError


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    live.reset_all()

    def down(messages):
        raise summarize.SummaryError("summary_failed", "no real model in tests")
    monkeypatch.setattr(summarize, "call_chat", down)
    yield
    live.reset_all()


def session_with(*turns):
    s = live.open_session("c1", "a1", [], "tok")
    s.turns = [{"speaker": sp, "text": tx} for sp, tx in turns]
    return s


def med(name, **fields):
    base = {"drug_name": name, "strength": None, "form": None, "dose_text": None, "frequency_text": None,
            "food": "any", "duration_days": None, "as_needed": False, "instructions": None}
    base.update(fields)
    return base


class Model:
    """A fake language model that answers with the given medicines and remembers what it was asked."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.asked = []

    def __call__(self, messages):
        self.asked.append(messages)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return reply if isinstance(reply, str) else json.dumps({"medicines": reply})


def entries(s):
    return {e["key"]: e for e in lm.public(s)}


# ---------------------------------------------------------------------------
# words to ticks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said,ticks", [
    ("every morning", ["morning"]),
    ("in the morning", ["morning"]),
    ("at night", ["night"]),
    ("before sleeping", ["night"]),
    ("at bedtime", ["night"]),
    ("after lunch", ["noon"]),
    ("in the afternoon", ["noon"]),
    ("in the evening", ["night"]),
    ("morning and night", ["morning", "night"]),
    ("morning, noon and night", ["morning", "noon", "night"]),
    ("twice a day", ["morning", "night"]),
    ("two times a day", ["morning", "night"]),
    ("bd", ["morning", "night"]),
    ("three times a day", ["morning", "noon", "night"]),
    ("thrice daily", ["morning", "noon", "night"]),
    ("1-0-1", ["morning", "night"]),
    ("1 - 1 - 1", ["morning", "noon", "night"]),
    ("0-0-1", ["night"]),
    ("1-0-0", ["morning"]),
    ("twice a day, morning and noon", ["morning", "noon"]),      # the words named win over the count
    ("once a day", []),                                           # no time named: the doctor chooses
    ("daily", []),
    ("every 8 hours", []),
    ("", []),
    (None, []),
])
def test_how_often_becomes_morning_noon_night_ticks(said, ticks):
    assert lm.times_from(said) == ticks


def test_ticks_become_the_clock_times_the_reminders_use():
    assert lm.dose_times_for(["morning", "night"]) == ["09:00", "21:00"]
    assert lm.dose_times_for(["night", "morning", "noon"]) == ["09:00", "14:00", "21:00"]
    assert lm.dose_times_for([]) == []


@pytest.mark.parametrize("one,two", [
    ("Pantoprazole", "pantoprazole tablet"),
    ("Pantoprazole", "PANTOPRAZOLE"),
    ("Dolo", "Dolo tab"),
])
def test_the_same_medicine_said_two_ways_has_one_key(one, two):
    assert lm.key_of(one) == lm.key_of(two) != ""


def test_different_medicines_have_different_keys():
    assert lm.key_of("Dolo") != lm.key_of("Crocin")


@pytest.mark.parametrize("said,yes", [
    ("Take pantoprazole 40 mg every morning", True),
    ("I will prescribe an antacid", True),
    ("Crocin twice a day after food", True),
    ("one tablet at night", True),
    ("I have a headache and my throat hurts", False),
    ("It started three days ago", False),
    ("Tell me about the fever", False),
])
def test_only_talk_about_medicines_wakes_the_model(said, yes):
    assert lm.has_cue(said) is yes


# ---------------------------------------------------------------------------
# what the doctor says becomes a row
# ---------------------------------------------------------------------------

def test_a_full_instruction_fills_every_field_it_names():
    s = session_with(("doctor", "Take Pantoprazole 40 mg every morning before food for 5 days."))
    model = Model([med("Pantoprazole", strength="40 mg", form="tablet", dose_text="1 tablet",
                       frequency_text="every morning", food="before", duration_days=5)])
    assert lm.update(s, chat=model) is True
    row = entries(s)["pantoprazole"]
    assert row["strength"] == "40 mg" and row["food"] == "before" and row["duration_days"] == 5
    assert row["times"] == ["morning"] and row["dose_times"] == ["09:00"]
    assert row["missing"] == [] and row["removed"] is False


def test_twice_a_day_ticks_morning_and_night():
    s = session_with(("doctor", "Paracetamol 500 mg twice a day after food for 3 days"))
    lm.update(s, chat=Model([med("Paracetamol", strength="500 mg", frequency_text="twice a day", food="after", duration_days=3)]))
    row = entries(s)["paracetamol"]
    assert row["times"] == ["morning", "night"] and row["dose_times"] == ["09:00", "21:00"]


def test_one_zero_one_ticks_morning_and_night():
    s = session_with(("doctor", "Cetirizine 10 mg, 1-0-1 for 5 days"))
    lm.update(s, chat=Model([med("Cetirizine", strength="10 mg", frequency_text="1-0-1", duration_days=5)]))
    assert entries(s)["cetirizine"]["times"] == ["morning", "night"]


def test_what_was_not_said_stays_empty_and_is_listed_as_missing():
    s = session_with(("doctor", "I will give you Cetirizine for the allergy."))
    lm.update(s, chat=Model([med("Cetirizine")]))
    row = entries(s)["cetirizine"]
    assert row["strength"] is None and row["times"] == [] and row["dose_times"] == [] and row["duration_days"] is None
    assert row["missing"] == ["timing", "days"]


def test_once_a_day_with_no_time_named_ticks_nothing():
    s = session_with(("doctor", "Take Montelukast once a day for 10 days."))
    lm.update(s, chat=Model([med("Montelukast", frequency_text="once a day", duration_days=10)]))
    row = entries(s)["montelukast"]
    assert row["times"] == [] and row["missing"] == ["timing"]


def test_a_medicine_taken_only_when_needed_has_no_times():
    s = session_with(("doctor", "Take paracetamol only if the fever comes back, at night if needed."))
    lm.update(s, chat=Model([med("Paracetamol", frequency_text="at night if needed", as_needed=True)]))
    row = entries(s)["paracetamol"]
    assert row["times"] == [] and row["dose_times"] == [] and row["missing"] == []


def test_two_medicines_in_one_conversation():
    s = session_with(("doctor", "Take Pantoprazole every morning before food. And Dolo twice a day after food for 3 days."))
    lm.update(s, chat=Model([
        med("Pantoprazole", frequency_text="every morning", food="before"),
        med("Dolo", frequency_text="twice a day", food="after", duration_days=3)]))
    rows = entries(s)
    assert set(rows) == {"pantoprazole", "dolo"} and rows["dolo"]["times"] == ["morning", "night"]


def test_the_speaker_labels_do_not_matter():
    s = session_with(("patient", "Take Pantoprazole every morning before food."))     # mis-labelled turn
    lm.update(s, chat=Model([med("Pantoprazole", frequency_text="every morning", food="before")]))
    assert "pantoprazole" in entries(s)


# ---------------------------------------------------------------------------
# nothing is invented
# ---------------------------------------------------------------------------

def test_a_medicine_whose_name_was_never_heard_is_dropped():
    s = session_with(("doctor", "Take one tablet at night."), ("patient", "Okay doctor"))
    lm.update(s, chat=Model([med("Amoxicillin", frequency_text="at night")]))
    assert entries(s) == {}


def test_a_strength_with_no_number_in_the_conversation_is_dropped():
    s = session_with(("doctor", "Take Cetirizine every night."))
    lm.update(s, chat=Model([med("Cetirizine", strength="500 mg", frequency_text="every night")]))
    assert entries(s)["cetirizine"]["strength"] is None


def test_days_with_no_number_in_the_conversation_are_dropped():
    s = session_with(("doctor", "Take Cetirizine every night."))
    lm.update(s, chat=Model([med("Cetirizine", frequency_text="every night", duration_days=7)]))
    assert entries(s)["cetirizine"]["duration_days"] is None


def test_days_said_in_words_or_as_a_week_are_kept():
    s = session_with(("doctor", "Take Cetirizine every night for a week."))
    lm.update(s, chat=Model([med("Cetirizine", frequency_text="every night", duration_days=7)]))
    assert entries(s)["cetirizine"]["duration_days"] == 7


def test_a_how_often_the_doctor_never_said_is_dropped():
    s = session_with(("doctor", "I will prescribe Cetirizine for you."))
    lm.update(s, chat=Model([med("Cetirizine", frequency_text="twice a day")]))
    row = entries(s)["cetirizine"]
    assert row["frequency_text"] is None and row["times"] == []


def test_a_timing_the_model_adds_on_its_own_is_ignored():
    """Ticks come from the words of frequency / instructions, never from a 'times' guess by the model."""
    s = session_with(("doctor", "Take Cetirizine for the allergy."))
    lm.update(s, chat=Model([{**med("Cetirizine"), "times": ["morning", "night"]}]))
    assert entries(s)["cetirizine"]["times"] == []


def test_the_model_cannot_flood_the_list():
    s = session_with(("doctor", "Take these tablets: " + " ".join(f"Drug{chr(97 + i % 26)}{i}" for i in range(40))))
    lm.update(s, chat=Model([med(f"Drug{chr(97 + i % 26)}{i}") for i in range(40)]))
    assert len(entries(s)) <= 20


def test_an_answer_that_is_not_json_changes_nothing_and_is_retried_later():
    s = session_with(("doctor", "Take Pantoprazole every morning"))
    assert lm.update(s, chat=Model("sorry, I cannot help")) is False
    assert entries(s) == {} and s.med_checked == 0                  # not marked as read: the next run tries again
    assert lm.update(s, chat=Model([med("Pantoprazole", frequency_text="every morning")])) is True


def test_a_model_that_is_down_changes_nothing_and_never_raises():
    s = session_with(("doctor", "Take Pantoprazole every morning"))

    def broken(messages):
        raise summarize.SummaryError("summary_failed", "down")
    assert lm.update(s, chat=broken) is False and s.med_checked == 0


def test_a_surprise_error_is_swallowed():
    s = session_with(("doctor", "Take Pantoprazole every morning"))

    def boom(messages):
        raise RuntimeError("boom")
    assert lm.update(s, chat=boom) is False


# ---------------------------------------------------------------------------
# changes, cancellations, repeats
# ---------------------------------------------------------------------------

def test_a_change_updates_the_same_row_and_bumps_its_version():
    s = session_with(("doctor", "Take Dolo twice a day after food."))
    lm.update(s, chat=Model([med("Dolo", frequency_text="twice a day", food="after")]))
    first = entries(s)["dolo"]["rev"]
    s.turns.append({"speaker": "doctor", "text": "Actually make the Dolo once a day at night."})
    lm.update(s, chat=Model([med("Dolo", frequency_text="once a day at night", food="after")]))
    row = entries(s)["dolo"]
    assert len(entries(s)) == 1 and row["times"] == ["night"] and row["rev"] > first


def test_a_reading_that_changes_nothing_does_not_bump_anything():
    s = session_with(("doctor", "Take Dolo twice a day."))
    reply = [med("Dolo", frequency_text="twice a day")]
    lm.update(s, chat=Model(reply))
    rev = entries(s)["dolo"]["rev"]
    s.turns.append({"speaker": "patient", "text": "Should I take it with water?"})
    assert lm.update(s, chat=Model(reply)) is False
    assert entries(s)["dolo"]["rev"] == rev


def test_a_later_reading_with_less_detail_never_erases_what_was_said():
    s = session_with(("doctor", "Take Dolo 650 mg twice a day for 3 days."))
    lm.update(s, chat=Model([med("Dolo", strength="650 mg", frequency_text="twice a day", duration_days=3)]))
    s.turns.append({"speaker": "doctor", "text": "Take the Dolo with plenty of water."})
    lm.update(s, chat=Model([med("Dolo")]))
    row = entries(s)["dolo"]
    assert row["strength"] == "650 mg" and row["duration_days"] == 3 and row["times"] == ["morning", "night"]


def test_the_doctor_cancelling_a_medicine_removes_it():
    s = session_with(("doctor", "Take Dolo twice a day."))
    lm.update(s, chat=Model([med("Dolo", frequency_text="twice a day")]))
    s.turns.append({"speaker": "doctor", "text": "No, don't take Dolo. Take Crocin at night instead."})
    lm.update(s, chat=Model([med("Crocin", frequency_text="at night")]))
    rows = entries(s)
    assert rows["dolo"]["removed"] is True and rows["crocin"]["removed"] is False and rows["crocin"]["times"] == ["night"]


@pytest.mark.parametrize("said", [
    "Stop the Dolo, it is not needed.",
    "Please do not take Dolo.",
    "You can avoid Dolo.",
    "Dolo is not needed now.",
])
def test_cancelling_phrases_are_understood(said):
    s = session_with(("doctor", "Take Dolo twice a day."))
    lm.update(s, chat=Model([med("Dolo", frequency_text="twice a day")]))
    s.turns.append({"speaker": "doctor", "text": said})
    lm.update(s, chat=Model([]))
    assert entries(s)["dolo"]["removed"] is True


def test_a_model_slip_that_leaves_a_medicine_out_does_not_remove_it():
    s = session_with(("doctor", "Take Dolo twice a day. Take Pantoprazole every morning."))
    lm.update(s, chat=Model([med("Dolo", frequency_text="twice a day"), med("Pantoprazole", frequency_text="every morning")]))
    s.turns.append({"speaker": "doctor", "text": "Also take the Cetirizine at night."})
    lm.update(s, chat=Model([med("Cetirizine", frequency_text="at night")]))                    # (the model forgot the other two)
    rows = entries(s)
    assert rows["dolo"]["removed"] is False and rows["pantoprazole"]["removed"] is False and "cetirizine" in rows


def test_a_cancelled_medicine_the_doctor_prescribes_again_comes_back():
    s = session_with(("doctor", "Take Dolo twice a day."))
    lm.update(s, chat=Model([med("Dolo", frequency_text="twice a day")]))
    s.turns.append({"speaker": "doctor", "text": "Don't take Dolo."})
    lm.update(s, chat=Model([]))
    s.turns.append({"speaker": "doctor", "text": "Sorry, take the Dolo at night after all."})
    lm.update(s, chat=Model([med("Dolo", frequency_text="at night")]))
    row = entries(s)["dolo"]
    assert row["removed"] is False and row["times"] == ["night"]


def test_the_same_medicine_twice_in_one_answer_is_one_row():
    s = session_with(("doctor", "Take Pantoprazole every morning, the pantoprazole tablet before food."))
    lm.update(s, chat=Model([med("Pantoprazole", frequency_text="every morning"), med("pantoprazole tablet", food="before")]))
    assert len(entries(s)) == 1


# ---------------------------------------------------------------------------
# when the model is asked
# ---------------------------------------------------------------------------

def test_talk_with_no_medicine_words_never_calls_the_model():
    s = session_with(("patient", "I have a headache and a sore throat since yesterday"), ("doctor", "Tell me more about it"))
    model = Model([])
    assert lm.update(s, chat=model) is False
    assert model.asked == [] and s.med_checked > 0


def test_the_model_reads_only_when_there_is_new_medicine_talk():
    s = session_with(("doctor", "Take Dolo twice a day."))
    model = Model([med("Dolo", frequency_text="twice a day")])
    lm.update(s, chat=model)
    s.turns.append({"speaker": "patient", "text": "Thank you doctor, I will visit again"})
    lm.update(s, chat=model)
    assert len(model.asked) == 1


def test_a_timing_said_a_moment_after_the_name_is_still_connected():
    s = session_with(("doctor", "I will give you Pantoprazole."))
    model = Model([med("Pantoprazole")], [med("Pantoprazole", frequency_text="every morning")])
    lm.update(s, chat=model)
    s.turns.append({"speaker": "doctor", "text": "Take it every morning."})
    lm.update(s, chat=model)
    assert entries(s)["pantoprazole"]["times"] == ["morning"]


def test_the_model_is_told_what_was_already_noted_and_the_rules():
    s = session_with(("doctor", "Take Dolo twice a day."))
    model = Model([med("Dolo", frequency_text="twice a day")])
    lm.update(s, chat=model)
    s.turns.append({"speaker": "doctor", "text": "And take Pantoprazole every morning."})
    lm.update(s, chat=model)
    system, user = model.asked[1][0]["content"], model.asked[1][1]["content"]
    assert "Dolo" in user and "already noted" in user.lower() and "Pantoprazole" in user
    assert "never suggest a medicine yourself" in system.lower() and "already take" in system.lower()


# ---------------------------------------------------------------------------
# the live session and the page's endpoints
# ---------------------------------------------------------------------------

def test_the_engine_run_also_reads_the_medicines_and_still_stores_the_cards():
    s = session_with(("patient", "I have chest pain since morning"), ("doctor", "Take Pantoprazole every morning before food."))
    s.words_since_run = 100
    stored = []
    assert live.claim_engine_run(s, now=1.0)

    def chat(messages):
        if "keep the list of medicines" in messages[0]["content"]:
            return json.dumps({"medicines": [med("Pantoprazole", frequency_text="every morning", food="before")]})
        return json.dumps({"cards": [{"kind": "ask", "text": "Ask when the pain began.", "evidence": "chest pain since morning"}]})

    live.run_engine(s, lambda cards: stored.extend(cards) or cards, chat=chat)
    assert any(c["kind"] == "red_flag" for c in stored) and "pantoprazole" in entries(s)
    assert s.error is None and s.running is False


def test_a_failed_medicine_reading_does_not_pause_live_assist():
    s = session_with(("doctor", "Take Pantoprazole every morning before food."))
    s.words_since_run = 100
    assert live.claim_engine_run(s, now=1.0)

    def chat(messages):
        if "keep the list of medicines" in messages[0]["content"]:
            raise summarize.SummaryError("summary_failed", "down")
        return json.dumps({"cards": []})

    live.run_engine(s, lambda cards: cards, chat=chat)
    assert s.error is None and s.running is False


def test_the_snapshot_carries_the_medicines():
    s = session_with(("doctor", "Take Dolo twice a day."))
    lm.update(s, chat=Model([med("Dolo", frequency_text="twice a day")]))
    shown = live.snapshot(s)["medicines"]
    assert [m["key"] for m in shown] == ["dolo"] and shown[0]["dose_times"] == ["09:00", "21:00"]


def test_the_snapshot_lists_changes_oldest_first():
    s = session_with(("doctor", "Take Dolo twice a day. Take Pantoprazole every morning."))
    lm.update(s, chat=Model([med("Dolo", frequency_text="twice a day"), med("Pantoprazole", frequency_text="every morning")]))
    s.turns.append({"speaker": "doctor", "text": "Make the Dolo three times a day."})
    lm.update(s, chat=Model([med("Dolo", frequency_text="three times a day")]))
    assert [m["key"] for m in live.snapshot(s)["medicines"]] == ["pantoprazole", "dolo"]


class FakeTeamC:
    error = None

    def __init__(self, token):
        pass

    def get_consultation(self, consultation_id):
        return {"consultation_id": consultation_id, "appointment_id": "a1", "consent_state": "granted", "turns": []}

    def list_live_suggestions(self, consultation_id, after=0):
        if FakeTeamC.error:
            raise FakeTeamC.error
        return []


app = FastAPI()
app.include_router(doctor_routes.router)
client = TestClient(app)
AUTH = {"Authorization": "Bearer doctor-token"}


@pytest.fixture
def api(monkeypatch):
    FakeTeamC.error = None
    monkeypatch.setattr(doctor_routes, "TeamC", FakeTeamC)
    monkeypatch.setattr(doctor_routes, "LIVE_ASSIST_ENABLED", True)


def test_the_live_state_route_returns_the_medicines(api):
    s = session_with(("doctor", "Take Dolo twice a day."))
    lm.update(s, chat=Model([med("Dolo", frequency_text="twice a day")]))
    body = client.get("/doctor/api/consultations/c1/live", headers=AUTH).json()
    assert [m["key"] for m in body["medicines"]] == ["dolo"]


def test_before_any_audio_there_are_no_medicines(api):
    assert client.get("/doctor/api/consultations/c1/live", headers=AUTH).json()["medicines"] == []


def test_ending_reads_the_last_words_and_hands_over_the_medicines(api, monkeypatch):
    session_with(("doctor", "Take Pantoprazole every morning before food."))
    monkeypatch.setattr(summarize, "call_chat", Model([med("Pantoprazole", frequency_text="every morning", food="before")]))
    body = client.post("/doctor/api/consultations/c1/live/end", headers=AUTH).json()
    assert body["ended"] is True and [m["key"] for m in body["medicines"]] == ["pantoprazole"]
    assert live.get_session("c1") is None


def test_ending_with_a_model_that_is_down_still_ends(api):
    session_with(("doctor", "Take Pantoprazole every morning before food."))
    body = client.post("/doctor/api/consultations/c1/live/end", headers=AUTH).json()
    assert body == {"ended": True, "medicines": []}


def test_ending_needs_the_doctor_to_own_the_consultation(api):
    session_with(("doctor", "Take Pantoprazole every morning."))
    FakeTeamC.error = TeamCError(403, "not yours")
    assert client.post("/doctor/api/consultations/c1/live/end", headers=AUTH).status_code == 403
    assert live.get_session("c1") is not None


def test_each_live_session_has_its_own_epoch_so_the_page_can_tell_a_new_one():
    first = session_with(("doctor", "Take Dolo twice a day."))
    lm.update(first, chat=Model([med("Dolo", frequency_text="twice a day")]))
    live.close_session("c1")
    second = session_with(("doctor", "Take Dolo twice a day."))
    lm.update(second, chat=Model([med("Dolo", frequency_text="twice a day")]))
    one, two = lm.public(first)[0], lm.public(second)[0]
    assert one["epoch"] and two["epoch"] and one["epoch"] != two["epoch"]
    assert one["rev"] == two["rev"] == 1                  # the revisions restart, which is why the epoch exists


@pytest.mark.parametrize("said,written", [
    ("40 milligrams", "40 mg"), ("500mg", "500 mg"), ("500 milligram", "500 mg"), ("10 MG", "10 mg"),
    ("250 micrograms", "250 mcg"), ("5 millilitres", "5 ml"), ("5ml", "5 ml"), ("1 tablet", "1 tablet"), (None, None),
])
def test_units_are_written_the_same_way(said, written):
    assert lm.tidy_units(said) == written


def test_a_translated_strength_is_tidied_and_not_repeated_as_the_dose():
    s = session_with(("doctor", "Take 500mg of paracetamol twice a day, three days after meals."))
    lm.update(s, chat=Model([med("Paracetamol", strength="500 milligrams", dose_text="500mg", frequency_text="twice a day", duration_days=3)]))
    row = entries(s)["paracetamol"]
    assert row["strength"] == "500 mg" and row["dose_text"] is None


def test_a_how_often_that_is_really_a_number_of_days_is_dropped_but_the_days_are_kept():
    s = session_with(("doctor", "One tablet of Cetirizine 10 milligram five days before going to sleep at night."))
    lm.update(s, chat=Model([med("Cetirizine", strength="10 mg", dose_text="1 tablet", frequency_text="5 days",
                                 duration_days=5, instructions="before going to sleep at night")]))
    row = entries(s)["cetirizine"]
    assert row["frequency_text"] is None and row["duration_days"] == 5 and row["times"] == ["night"]
