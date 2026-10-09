"""Prescriptions: drafting, versioning, signing, dose scheduling, taking doses, missed doses."""
from datetime import datetime, time, timedelta

import pytest

from app.models.agent import AgentEvent, AgentMessage
from app.models.ai_appointment import AIAppointment
from app.models.prescription import DoseStatus, MedicationDose, Prescription
from app.models.reminder import Reminder
from app.services import agent_service, reminder_service
from app.services import prescription_service as rx
from app.services.slot_service import IST, as_utc, utcnow
from helpers_stage3 import book, client, day_ist, db, make_doctor, phone, quiet_world  # noqa: F401

PARA = {"drug_name": "Paracetamol", "strength": "500 mg", "dose_text": "1 tablet",
        "frequency_text": "1-0-1", "food": "after", "duration_days": 3}
COUGH = {"drug_name": "Cough syrup", "dose_text": "10 ml", "frequency_text": "thrice a day", "duration_days": 2}


def seven_am() -> datetime:
    """Today 07:00 IST: every dose time of the day is still ahead."""
    return datetime.combine(day_ist(0), time(7, 0), tzinfo=IST)


# ---------------------------------------------------------------------------
# set-up
# ---------------------------------------------------------------------------

@pytest.fixture
def visit():
    session = db()
    doctor = make_doctor(session, "Owner")
    other = make_doctor(session, "Other")
    session.close()
    patient_phone = phone()
    patient = client.post("/api/v1/auth/phone-login", json={"phone_no": patient_phone}).json()
    appointment_id, session_id, slot = book(doctor["id"], patient["auth_id"], slot_index=0, days_ahead=2)
    client.post(f"/api/v1/appointments/{appointment_id}/consent", json={"consent_given": True, "auth_id": patient["auth_id"]})
    consultation = client.post(
        "/api/v1/consultations", headers=doctor["headers"], json={"appointment_id": appointment_id, "mode": "online"}
    ).json()
    return {
        "doctor": doctor, "other": other, "appointment_id": appointment_id, "patient_auth_id": patient["auth_id"],
        "phone": patient_phone, "consultation_id": consultation["consultation_id"],
    }


def put_rx(visit, items, source="doctor_edit", headers=None):
    return client.put(
        f"/api/v1/consultations/{visit['consultation_id']}/prescription",
        headers=headers or visit["doctor"]["headers"], json={"items": items, "source": source},
    )


def sign_rx(visit, headers=None):
    return client.post(
        f"/api/v1/consultations/{visit['consultation_id']}/prescription/sign", headers=headers or visit["doctor"]["headers"]
    )


def signed_and_scheduled(visit, items=None, now=None):
    assert put_rx(visit, items or [PARA]).status_code == 200
    assert sign_rx(visit).status_code == 200
    session = db()
    result = agent_service.process_all(session, now or seven_am())
    session.close()
    return result


def flush_booking_notices(visit):
    """Send the notices made at booking time first, so a test about dose reminders counts only those."""
    session = db()
    reminder_service.process_due(session, now=utcnow() + timedelta(seconds=5), appointment_id=visit["appointment_id"])
    session.close()


def doses(visit):
    session = db()
    try:
        return session.query(MedicationDose).filter(MedicationDose.appointment_id == visit["appointment_id"]).order_by(MedicationDose.due_at).all()
    finally:
        session.close()


def medication_reminders(visit):
    session = db()
    try:
        return session.query(Reminder).filter(
            Reminder.appointment_id == visit["appointment_id"], Reminder.kind == "medication"
        ).all()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# reading a frequency
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,times", [
    ("1-0-1", ["08:00", "21:00"]), ("1-1-1", ["08:00", "14:00", "21:00"]), ("0-0-1", ["21:00"]),
    ("1-0-0", ["08:00"]), ("0-1-0", ["14:00"]), ("1-1-0", ["08:00", "14:00"]),
    ("1-1-1-1", ["08:00", "12:00", "16:00", "21:00"]),
    ("once a day", ["08:00"]), ("OD", ["08:00"]), ("twice a day", ["08:00", "21:00"]), ("BD", ["08:00", "21:00"]),
    ("thrice daily", ["08:00", "14:00", "21:00"]), ("TDS", ["08:00", "14:00", "21:00"]),
    ("four times a day", ["08:00", "12:00", "16:00", "20:00"]),
    ("", []), ("as needed", []), ("whenever", []),
])
def test_a_frequency_becomes_clock_times(text, times):
    assert rx.times_for(text) == times


# ---------------------------------------------------------------------------
# drafting
# ---------------------------------------------------------------------------

def test_a_doctor_saves_a_draft_and_the_times_come_from_the_frequency(visit):
    response = put_rx(visit, [PARA, COUGH])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "draft" and body["version"] == 1
    assert [i["dose_times"] for i in body["items"]] == [["08:00", "21:00"], ["08:00", "14:00", "21:00"]]
    assert body["items"][0]["drug_name"] == "Paracetamol" and body["items"][0]["food"] == "after"


def test_saving_again_replaces_the_draft_instead_of_adding_versions(visit):
    put_rx(visit, [PARA])
    again = put_rx(visit, [COUGH]).json()
    assert again["version"] == 1 and [i["drug_name"] for i in again["items"]] == ["Cough syrup"]


def test_an_ai_draft_is_marked_so_the_doctor_verifies_it(visit):
    body = put_rx(visit, [{**PARA, "from_transcript": True}], source="transcript").json()
    assert body["source"] == "transcript" and body["items"][0]["from_transcript"] is True


def test_the_doctor_can_set_their_own_dose_times(visit):
    body = put_rx(visit, [{**PARA, "dose_times": ["09:30", "22:00"]}]).json()
    assert body["items"][0]["dose_times"] == ["09:30", "22:00"]


@pytest.mark.parametrize("item,fragment", [
    ({"drug_name": "  "}, "name"),
    ({**PARA, "dose_times": ["25:00"]}, "not a time"),
    ({**PARA, "dose_times": ["8am"]}, "not a time"),
    ({**PARA, "duration_days": 0}, "between 1 and 90"),
    ({**PARA, "duration_days": 91}, "between 1 and 90"),
    ({**PARA, "duration_days": "many"}, "integer"),
    ({**PARA, "dose_times": ["01:00", "02:00", "03:00", "04:00", "05:00", "06:00", "07:00"]}, "at most"),
])
def test_bad_medicine_lines_are_refused_with_a_reason(visit, item, fragment):
    response = put_rx(visit, [item])
    assert response.status_code == 422
    assert fragment in response.text


def test_at_most_twenty_medicines(visit):
    assert put_rx(visit, [{"drug_name": f"Drug {i}"} for i in range(21)]).status_code == 422


def test_only_the_treating_doctor_can_see_or_change_the_prescription(visit):
    put_rx(visit, [PARA])
    other = visit["other"]["headers"]
    assert put_rx(visit, [PARA], headers=other).status_code == 403
    assert sign_rx(visit, headers=other).status_code == 403
    assert client.get(f"/api/v1/consultations/{visit['consultation_id']}/prescription", headers=other).status_code == 403
    assert client.get(f"/api/v1/consultations/{visit['consultation_id']}/prescription").status_code == 401


def test_the_card_gets_everything_in_one_call(visit):
    state = client.get(f"/api/v1/consultations/{visit['consultation_id']}/prescription", headers=visit["doctor"]["headers"]).json()
    assert state == {"current": None, "signed": None, "can_carry_forward": False}
    put_rx(visit, [PARA])
    state = client.get(f"/api/v1/consultations/{visit['consultation_id']}/prescription", headers=visit["doctor"]["headers"]).json()
    assert state["current"]["status"] == "draft" and state["signed"] is None


# ---------------------------------------------------------------------------
# signing
# ---------------------------------------------------------------------------

def test_there_is_nothing_to_sign_without_a_draft_or_medicines(visit):
    assert sign_rx(visit).status_code == 409
    put_rx(visit, [])
    assert sign_rx(visit).status_code == 409


def test_a_medicine_without_dose_times_blocks_signing_unless_it_is_only_when_needed(visit):
    put_rx(visit, [{"drug_name": "Mystery", "frequency_text": "whenever"}])
    response = sign_rx(visit)
    assert response.status_code == 422 and "dose times" in response.text
    put_rx(visit, [{"drug_name": "Mystery", "as_needed": True}])
    assert sign_rx(visit).status_code == 200


def test_signing_locks_the_version_and_a_change_is_the_next_version(visit):
    put_rx(visit, [PARA])
    signed = sign_rx(visit).json()
    assert signed["status"] == "signed" and signed["signed_at"]
    assert sign_rx(visit).status_code == 409                    # nothing left to sign
    changed = put_rx(visit, [COUGH]).json()
    assert changed["version"] == 2 and changed["status"] == "draft"
    state = client.get(f"/api/v1/consultations/{visit['consultation_id']}/prescription", headers=visit["doctor"]["headers"]).json()
    assert state["signed"]["version"] == 1 and state["current"]["version"] == 2     # v1 stays in force until v2 is signed


def test_a_draft_schedules_nothing_and_tells_nobody(visit):
    put_rx(visit, [PARA])
    session = db()
    assert agent_service.process_all(session, seven_am()) is not None
    session.close()
    assert doses(visit) == [] and medication_reminders(visit) == []


def test_signing_queues_one_event_and_writes_an_audit_entry(visit):
    put_rx(visit, [PARA])
    sign_rx(visit)
    session = db()
    events = [e for e in session.query(AgentEvent).filter(AgentEvent.appointment_id == visit["appointment_id"]) if e.kind == "prescription_signed"]
    assert len(events) == 1 and events[0].status == "pending"
    from app.models.agent import AgentAction
    tools = {a.tool for a in session.query(AgentAction).filter(AgentAction.appointment_id == visit["appointment_id"])}
    session.close()
    assert {"save_prescription_draft", "sign_prescription"} <= tools


# ---------------------------------------------------------------------------
# the coordinator schedules the doses
# ---------------------------------------------------------------------------

def test_once_signed_every_dose_gets_a_time_and_a_reminder(visit):
    result = signed_and_scheduled(visit)
    assert result.get("done", 0) >= 1
    scheduled = doses(visit)
    assert len(scheduled) == 6                                   # 3 days x 08:00 and 21:00
    assert {as_utc(d.due_at).astimezone(IST).strftime("%H:%M") for d in scheduled} == {"08:00", "21:00"}
    assert all(d.status == "scheduled" and d.patient_auth_id == visit["patient_auth_id"] for d in scheduled)
    reminders = medication_reminders(visit)
    assert len(reminders) == 6 and all(r.recipient_type == "patient" and r.status == "pending" for r in reminders)
    assert {r.dose_id for r in reminders} == {d.dose_id for d in scheduled}


def test_handling_the_same_event_twice_does_not_double_the_doses(visit):
    signed_and_scheduled(visit)
    session = db()
    prescription = session.query(Prescription).filter(Prescription.appointment_id == visit["appointment_id"]).first()
    assert rx.schedule_doses(session, prescription, seven_am()) == 0
    session.close()
    assert len(doses(visit)) == 6


def test_doses_that_are_already_past_are_not_created(visit):
    nine_thirty_pm = datetime.combine(day_ist(0), time(21, 30), tzinfo=IST)
    signed_and_scheduled(visit, now=nine_thirty_pm)
    today = [d for d in doses(visit) if as_utc(d.due_at).astimezone(IST).date() == day_ist(0)]
    assert today == []                                           # both of today's times have passed
    assert len(doses(visit)) == 4                                # tomorrow and the day after: 2 x 2
    assert all(as_utc(d.due_at) > as_utc(nine_thirty_pm) for d in doses(visit))


def test_a_medicine_with_no_length_runs_for_the_default_days(visit):
    signed_and_scheduled(visit, items=[{"drug_name": "Vitamin D", "frequency_text": "once a day"}])
    assert len(doses(visit)) == 7


def test_only_when_needed_medicines_have_no_doses(visit):
    signed_and_scheduled(visit, items=[{"drug_name": "Antacid", "as_needed": True}])
    assert doses(visit) == []


def test_the_doctor_and_the_patient_are_each_told(visit):
    signed_and_scheduled(visit)
    session = db()
    rows = session.query(AgentMessage).filter(AgentMessage.appointment_id == visit["appointment_id"], AgentMessage.kind == "prescription_signed").all()
    session.close()
    by_type = {r.recipient_type: r for r in rows}
    assert "Prescription signed" in by_type["doctor"].text and "6 dose reminder" in by_type["doctor"].text
    assert "prescribed 1 medicine" in by_type["patient"].text
    assert by_type["doctor"].recipient_auth_id == visit["doctor"]["auth_id"] and by_type["patient"].recipient_auth_id == visit["patient_auth_id"]


def test_signing_a_new_version_cancels_the_old_versions_future_doses(visit):
    signed_and_scheduled(visit)
    old_doses = [d.dose_id for d in doses(visit)]
    signed_and_scheduled(visit, items=[COUGH])
    session = db()
    old = session.query(MedicationDose).filter(MedicationDose.dose_id.in_(old_doses)).all()
    old_reminders = session.query(Reminder).filter(Reminder.dose_id.in_(old_doses)).all()
    session.close()
    assert all(d.status == "cancelled" for d in old) and all(r.status == "cancelled" for r in old_reminders)
    fresh = [d for d in doses(visit) if d.status == "scheduled"]
    assert len(fresh) == 6                                       # 2 days x 3 times
    first = session = db()
    versions = {p.version: p.status for p in session.query(Prescription).filter(Prescription.appointment_id == visit["appointment_id"])}
    first.close()
    assert versions == {1: "superseded", 2: "signed"}


# ---------------------------------------------------------------------------
# the reminder itself
# ---------------------------------------------------------------------------

def test_a_dose_reminder_names_the_medicine_and_never_a_diagnosis(visit):
    signed_and_scheduled(visit)
    reminder = sorted(medication_reminders(visit), key=lambda r: as_utc(r.send_at))[0]
    session = db()
    appointment = session.get(AIAppointment, visit["appointment_id"])
    from app.models.authentication import Authentication
    patient_name = session.get(Authentication, visit["patient_auth_id"]).name or "there"
    text, template, parameters = reminder_service.build_message(session, session.get(Reminder, reminder.reminder_id), appointment)
    session.close()
    assert text == "Time to take your medicine: Paracetamol 500 mg, 1 tablet, after food."
    assert template == "zenvy_medication_reminder"
    assert dict(parameters) == {"name": patient_name, "medicine": "Paracetamol 500 mg", "dose": "1 tablet", "food": "after food"}


def test_the_scheduler_sends_a_due_dose_reminder_in_mock_mode(visit):
    signed_and_scheduled(visit)
    flush_booking_notices(visit)
    first = sorted(doses(visit), key=lambda d: as_utc(d.due_at))[0]
    session = db()
    counts = reminder_service.process_due(session, now=as_utc(first.due_at) + timedelta(minutes=1), appointment_id=visit["appointment_id"])
    row = session.query(Reminder).filter(Reminder.dose_id == first.dose_id).one()
    session.close()
    assert counts.get("sent") == 1 and row.status == "sent" and row.mode == "mock"
    assert row.message_text.startswith("Time to take your medicine")


def test_a_dose_taken_before_its_reminder_is_not_reminded(visit):
    signed_and_scheduled(visit)
    flush_booking_notices(visit)
    first = sorted(doses(visit), key=lambda d: as_utc(d.due_at))[0]
    session = db()
    rx.mark_taken(session, first.dose_id, visit["patient_auth_id"], now=as_utc(first.due_at) - timedelta(minutes=10))
    counts = reminder_service.process_due(session, now=as_utc(first.due_at) + timedelta(minutes=1), appointment_id=visit["appointment_id"])
    row = session.query(Reminder).filter(Reminder.dose_id == first.dose_id).one()
    session.close()
    assert "sent" not in counts and row.status == "cancelled"


def test_a_reminder_that_is_hours_late_is_skipped_not_sent(visit):
    signed_and_scheduled(visit)
    flush_booking_notices(visit)
    first = sorted(doses(visit), key=lambda d: as_utc(d.due_at))[0]
    session = db()
    reminder_service.process_due(session, now=as_utc(first.due_at) + timedelta(hours=6), appointment_id=visit["appointment_id"], limit=1)
    row = session.query(Reminder).filter(Reminder.dose_id == first.dose_id).one()
    session.close()
    assert row.status == "skipped" and "too late" in row.last_error


def test_cancelling_the_visit_does_not_stop_the_medicine_reminders(visit):
    signed_and_scheduled(visit)
    session = db()
    reminder_service.cancel_pending(session, visit["appointment_id"])
    still = session.query(Reminder).filter(Reminder.appointment_id == visit["appointment_id"], Reminder.kind == "medication", Reminder.status == "pending").count()
    session.close()
    assert still == 6


def test_the_visit_history_does_not_list_every_dose_reminder(visit):
    signed_and_scheduled(visit)
    history = client.get(f"/api/v1/appointments/{visit['appointment_id']}/history", headers=visit["doctor"]["headers"]).json()
    assert all(r["kind"] != "medication" for r in history["reminders"])


# ---------------------------------------------------------------------------
# taking and missing doses
# ---------------------------------------------------------------------------

def test_the_patient_marks_a_dose_taken_when_it_is_due(visit):
    signed_and_scheduled(visit)
    first = sorted(doses(visit), key=lambda d: as_utc(d.due_at))[0]
    session = db()
    taken = rx.mark_taken(session, first.dose_id, visit["patient_auth_id"], now=as_utc(first.due_at) + timedelta(minutes=5))
    again = rx.mark_taken(session, first.dose_id, visit["patient_auth_id"], now=as_utc(first.due_at) + timedelta(minutes=9))
    session.close()
    assert taken.status == "taken" and again.status == "taken"       # saying it twice is harmless


def test_a_dose_that_is_far_in_the_future_cannot_be_ticked_off(visit):
    signed_and_scheduled(visit)
    last = sorted(doses(visit), key=lambda d: as_utc(d.due_at))[-1]
    response = client.post(f"/api/v1/patients/{visit['patient_auth_id']}/doses/{last.dose_id}/taken")
    assert response.status_code == 409 and response.json()["detail"] == "too_early"


def test_nobody_else_can_mark_my_dose(visit):
    signed_and_scheduled(visit)
    first = sorted(doses(visit), key=lambda d: as_utc(d.due_at))[0]
    stranger = client.post("/api/v1/auth/phone-login", json={"phone_no": phone()}).json()["auth_id"]
    response = client.post(f"/api/v1/patients/{stranger}/doses/{first.dose_id}/taken")
    assert response.status_code == 404
    assert client.post(f"/api/v1/patients/not-a-person/doses/{first.dose_id}/taken").status_code == 404


def test_i_took_my_medicine_marks_the_latest_dose_time_only(visit):
    signed_and_scheduled(visit, items=[PARA, COUGH])
    ordered = sorted(doses(visit), key=lambda d: as_utc(d.due_at))
    eight = [d for d in ordered if as_utc(d.due_at).astimezone(IST).strftime("%H:%M") == "08:00"]
    now = as_utc(eight[0].due_at) + timedelta(minutes=30)                  # both medicines are due at 08:00
    session = db()
    taken = rx.mark_taken_now(session, visit["patient_auth_id"], now)
    session.close()
    assert len(taken) == 2                                                  # paracetamol and the syrup, together
    assert rx.mark_taken_now(db(), visit["patient_auth_id"], now) == []     # nothing left to mark


def test_a_dose_nobody_took_becomes_missed(visit):
    signed_and_scheduled(visit)
    first = sorted(doses(visit), key=lambda d: as_utc(d.due_at))[0]
    session = db()
    missed = rx.sweep_missed(session, as_utc(first.due_at) + timedelta(hours=4))
    again = rx.sweep_missed(session, as_utc(first.due_at) + timedelta(hours=4))
    row = session.get(MedicationDose, first.dose_id)
    session.close()
    assert sum(missed.values()) >= 1 and row.status == "missed" and sum(again.values()) == 0


def test_a_missed_dose_can_still_be_marked_taken_late(visit):
    signed_and_scheduled(visit)
    first = sorted(doses(visit), key=lambda d: as_utc(d.due_at))[0]
    late = as_utc(first.due_at) + timedelta(hours=5)
    session = db()
    rx.sweep_missed(session, late)
    assert rx.mark_taken(session, first.dose_id, visit["patient_auth_id"], now=late).status == "taken"
    session.close()


def test_missing_two_doses_in_a_day_tells_the_doctor_once(visit):
    signed_and_scheduled(visit, items=[COUGH])
    ordered = sorted(doses(visit), key=lambda d: as_utc(d.due_at))
    later = as_utc(ordered[2].due_at) + timedelta(hours=4)               # the first three doses have passed
    session = db()
    mine = session.query(Prescription).filter(Prescription.appointment_id == visit["appointment_id"]).one().prescription_id
    missed = rx.sweep_missed(session, later)
    missed = type(missed)({pid: n for pid, n in missed.items() if pid == mine})       # the test database is shared
    assert agent_service.check_adherence(session, missed, later) == 1
    assert agent_service.check_adherence(session, missed, later) == 0   # the same day: no second alert
    agent_service.process_all(session, later)
    notes = session.query(AgentMessage).filter(AgentMessage.appointment_id == visit["appointment_id"], AgentMessage.kind == "adherence_alert").all()
    session.close()
    assert len(notes) == 1 and "missed" in notes[0].text and "Cough syrup" in notes[0].text
    assert notes[0].recipient_auth_id == visit["doctor"]["auth_id"]


def test_the_doctor_sees_how_each_medicine_is_going(visit):
    signed_and_scheduled(visit)
    first = sorted(doses(visit), key=lambda d: as_utc(d.due_at))[0]
    session = db()
    rx.mark_taken(session, first.dose_id, visit["patient_auth_id"], now=as_utc(first.due_at))
    session.close()
    state = client.get(f"/api/v1/consultations/{visit['consultation_id']}/prescription", headers=visit["doctor"]["headers"]).json()
    assert state["signed"]["items"][0]["adherence"] == {"taken": 1, "missed": 0, "scheduled": 5, "cancelled": 0}


# ---------------------------------------------------------------------------
# what the patient sees
# ---------------------------------------------------------------------------

def test_your_medicines_lists_the_signed_medicines_and_the_next_dose(visit):
    signed_and_scheduled(visit, now=utcnow() - timedelta(minutes=1))
    body = client.get(f"/api/v1/patients/{visit['patient_auth_id']}/medications").json()
    assert len(body["prescriptions"]) == 1
    item = body["prescriptions"][0]["items"][0]
    assert item["drug_name"] == "Paracetamol" and item["next_dose_at"] and item["remaining_doses"] >= 4
    assert body["prescriptions"][0]["doctor_name"] == visit["doctor"]["name"]
    assert any("prescribed 1 medicine" in m["text"] for m in body["messages"])


def test_a_draft_is_never_shown_to_the_patient(visit):
    put_rx(visit, [PARA])
    body = client.get(f"/api/v1/patients/{visit['patient_auth_id']}/medications").json()
    assert body["prescriptions"] == []


def test_another_patient_sees_nothing_of_mine(visit):
    signed_and_scheduled(visit, now=utcnow() - timedelta(minutes=1))
    stranger = client.post("/api/v1/auth/phone-login", json={"phone_no": phone()}).json()["auth_id"]
    assert client.get(f"/api/v1/patients/{stranger}/medications").json()["prescriptions"] == []
    assert client.get("/api/v1/patients/nobody/medications").status_code == 404


def test_a_finished_course_disappears_from_your_medicines(visit):
    signed_and_scheduled(visit, items=[{**PARA, "duration_days": 1}], now=seven_am())
    session = db()
    after = seven_am() + timedelta(days=3)
    shown = rx.medications_for_patient(session, visit["patient_auth_id"], after)
    session.close()
    assert shown == []


def test_the_message_for_the_patient_is_marked_read_once_seen(visit):
    signed_and_scheduled(visit, now=utcnow() - timedelta(minutes=1))
    assert client.post(f"/api/v1/patients/{visit['patient_auth_id']}/messages/read", json={}).json()["marked"] >= 1
    assert client.get(f"/api/v1/patients/{visit['patient_auth_id']}/medications").json()["messages"] == []


# ---------------------------------------------------------------------------
# follow-up: carry the medicines forward
# ---------------------------------------------------------------------------

def test_at_a_follow_up_the_signed_medicines_can_be_carried_forward(visit):
    signed_and_scheduled(visit)
    follow_up_id, _, _ = book(visit["doctor"]["id"], visit["patient_auth_id"], slot_index=1, days_ahead=9)
    session = db()
    follow_up = session.get(AIAppointment, follow_up_id)
    follow_up.parent_appointment_id = visit["appointment_id"]
    follow_up.appointment_type = "follow_up"
    session.commit()
    session.close()
    client.post(f"/api/v1/appointments/{follow_up_id}/consent", json={"consent_given": True, "auth_id": visit["patient_auth_id"]})
    consultation = client.post("/api/v1/consultations", headers=visit["doctor"]["headers"], json={"appointment_id": follow_up_id, "mode": "online"}).json()["consultation_id"]

    state = client.get(f"/api/v1/consultations/{consultation}/prescription", headers=visit["doctor"]["headers"]).json()
    assert state["can_carry_forward"] is True
    carried = client.post(f"/api/v1/consultations/{consultation}/prescription/carry-forward", headers=visit["doctor"]["headers"])
    assert carried.status_code == 200
    body = carried.json()
    assert body["status"] == "draft" and body["source"] == "carried_forward"
    assert body["items"][0]["drug_name"] == "Paracetamol" and body["items"][0]["from_transcript"] is False


def test_a_first_visit_has_nothing_to_carry_forward(visit):
    response = client.post(f"/api/v1/consultations/{visit['consultation_id']}/prescription/carry-forward", headers=visit["doctor"]["headers"])
    assert response.status_code == 409


def test_the_doctor_list_shows_the_prescription_status(visit):
    put_rx(visit, [PARA])
    listed = client.get("/api/v1/doctor/appointments", headers=visit["doctor"]["headers"]).json()
    row = next(a for a in listed if a["appointment_id"] == visit["appointment_id"])
    assert row["prescription_status"] == "draft"
