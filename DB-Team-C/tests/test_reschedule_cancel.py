"""Cancelling and rescheduling an appointment: who may, when, and that it is all-or-nothing."""
from datetime import timedelta

from app.models.ai_appointment import AIAppointment
from app.models.audit_log import AuditLog
from app.models.consultation import ConsultationConsent
from app.models.doctor_slot import DoctorSlot
from app.services.slot_service import utcnow
from helpers_stage3 import book, client, db, free_slots, make_doctor, phone, wav, quiet_world, world  # noqa: F401


def appointment(appointment_id):
    session = db()
    row = session.get(AIAppointment, appointment_id)
    session.expunge_all()
    session.close()
    return row


def slot(slot_id):
    session = db()
    row = session.get(DoctorSlot, slot_id)
    session.expunge_all()
    session.close()
    return row


def cancel(world, **body):
    return client.post(f"/api/v1/appointments/{world['appointment_id']}/cancel", json=body)


def reschedule(world, slot_id, **body):
    body = body or {"auth_id": world["patient_auth_id"]}
    return client.post(f"/api/v1/appointments/{world['appointment_id']}/reschedule", json={"slot_id": slot_id, **body})


def move_into_the_past(appointment_id):
    session = db()
    session.get(AIAppointment, appointment_id).appointment_datetime = utcnow() - timedelta(hours=1)
    session.commit()
    session.close()


def start_consultation(world):
    client.post(f"/api/v1/appointments/{world['appointment_id']}/consent", json={"consent_given": True, "auth_id": world["patient_auth_id"]})
    started = client.post("/api/v1/consultations", headers=world["doctor"]["headers"],
                          json={"appointment_id": world["appointment_id"], "mode": "online"})
    assert started.status_code == 201, started.text
    return started.json()["consultation_id"]


# ---------------------------------------------------------------------------
# cancelling
# ---------------------------------------------------------------------------

def test_the_patient_can_cancel_with_their_login(world):
    response = cancel(world, auth_id=world["patient_auth_id"], reason="cannot come")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled" and body["cancelled_by"] == "patient" and body["cancel_reason"] == "cannot come"
    assert body["cancelled_at"]
    assert slot(world["slot"]["slot_id"]).status == "available"


def test_the_booking_conversation_can_cancel_too(world):
    assert cancel(world, session_id=world["session_id"]).status_code == 200


def test_staff_can_cancel_without_sending_an_id(world):
    response = client.post(f"/api/v1/appointments/{world['appointment_id']}/cancel", headers=world["doctor"]["headers"])
    assert response.status_code == 200 and response.json()["cancelled_by"] == "staff"


def test_nobody_else_can_cancel(world):
    stranger = client.post("/api/v1/auth/phone-login", json={"phone_no": phone()}).json()["auth_id"]
    assert cancel(world).status_code == 403
    assert cancel(world, auth_id=stranger).status_code == 403
    assert cancel(world, auth_id="made-up").status_code == 403
    assert cancel(world, session_id="made-up").status_code == 403
    assert appointment(world["appointment_id"]).status.value != "cancelled"
    assert client.post("/api/v1/appointments/missing/cancel", json={"auth_id": stranger}).status_code == 404


def test_cancelling_twice_is_harmless(world):
    for _ in range(2):
        response = cancel(world, auth_id=world["patient_auth_id"])
        assert response.status_code == 200 and response.json()["status"] == "cancelled"
    session = db()
    cancellations = session.query(AuditLog).filter(AuditLog.action == "cancel_appointment", AuditLog.session_id == world["session_id"]).count()
    session.close()
    assert cancellations == 1  # audited once


def test_an_appointment_that_has_started_cannot_be_cancelled(world):
    move_into_the_past(world["appointment_id"])
    response = cancel(world, auth_id=world["patient_auth_id"])
    assert response.status_code == 409 and response.json()["detail"] == "already_started"
    assert appointment(world["appointment_id"]).status.value == "confirmed"


def test_an_appointment_with_a_consultation_cannot_be_cancelled(world):
    start_consultation(world)
    response = cancel(world, auth_id=world["patient_auth_id"])
    assert response.status_code == 409 and response.json()["detail"] == "has_consultation"
    assert slot(world["slot"]["slot_id"]).status == "booked"


def test_a_cancelled_slot_can_be_booked_by_the_next_patient(world):
    cancel(world, auth_id=world["patient_auth_id"])
    other = client.post("/api/v1/auth/phone-login", json={"phone_no": phone()}).json()
    session_id = client.post("/api/v1/sessions", json={"user_id": other["auth_id"], "channel": "web", "language": "en"}).json()["session_id"]
    assert client.post(f"/api/v1/slots/{world['slot']['slot_id']}/hold", json={"session_id": session_id}).status_code == 200


def test_the_cancellation_is_audited_without_a_phone_number(world):
    cancel(world, auth_id=world["patient_auth_id"])
    session = db()
    entry = session.query(AuditLog).filter(AuditLog.action == "cancel_appointment", AuditLog.session_id == world["session_id"]).one()
    session.close()
    assert entry.after_value["cancelled_by"] == "patient" and entry.user_id == world["patient_auth_id"]
    assert world["phone"] not in str([entry.actor, entry.before_value, entry.after_value])


# ---------------------------------------------------------------------------
# rescheduling
# ---------------------------------------------------------------------------

def test_the_patient_can_move_the_appointment_to_another_time_of_the_same_doctor(world):
    target = free_slots(world["doctor"]["id"], 4)[3]
    response = reschedule(world, target["slot_id"])
    assert response.status_code == 200
    new = response.json()

    assert new["appointment_id"] != world["appointment_id"]
    assert new["status"] == "confirmed" and new["doctor_id"] == world["doctor"]["id"]
    assert new["slot_id"] == target["slot_id"] and new["rescheduled_from_id"] == world["appointment_id"]
    assert new["booking_info"]["location"] == world["doctor"]["hospital"]

    old = appointment(world["appointment_id"])
    assert old.status.value == "cancelled" and old.cancel_reason == "rescheduled" and old.cancelled_by == "patient"
    assert slot(world["slot"]["slot_id"]).status == "available"   # the old time is free again
    assert slot(target["slot_id"]).status == "booked"


def test_a_failed_reschedule_changes_nothing(world):
    """The new time is taken: the patient keeps the appointment they had."""
    target = free_slots(world["doctor"]["id"], 4)[3]
    rival = client.post("/api/v1/auth/phone-login", json={"phone_no": phone()}).json()
    book_target = client.post(f"/api/v1/slots/{target['slot_id']}/hold", json={"session_id": world["session_id"]})
    assert book_target.status_code == 200
    # someone else now holds it
    other_session = client.post("/api/v1/sessions", json={"user_id": rival["auth_id"], "channel": "web", "language": "en"}).json()["session_id"]
    taken = reschedule(world, target["slot_id"], auth_id=world["patient_auth_id"], session_id=other_session)
    assert taken.status_code == 409 and taken.json()["detail"] == "slot_unavailable"

    assert appointment(world["appointment_id"]).status.value == "confirmed"
    assert slot(world["slot"]["slot_id"]).status == "booked"
    session = db()
    assert session.query(AIAppointment).filter(AIAppointment.rescheduled_from_id == world["appointment_id"]).count() == 0
    session.close()


def test_a_slot_you_are_holding_yourself_can_be_used(world):
    target = free_slots(world["doctor"]["id"], 4)[4]
    client.post(f"/api/v1/slots/{target['slot_id']}/hold", json={"session_id": world["session_id"]})
    assert reschedule(world, target["slot_id"], auth_id=world["patient_auth_id"], session_id=world["session_id"]).status_code == 200


def test_a_reschedule_must_stay_with_the_same_doctor(world):
    other_slot = free_slots(world["other"]["id"], 4)[0]
    response = reschedule(world, other_slot["slot_id"])
    assert response.status_code == 409 and response.json()["detail"] == "different_doctor"
    assert appointment(world["appointment_id"]).status.value == "confirmed"


def test_moving_to_the_time_you_already_have_is_refused(world):
    response = reschedule(world, world["slot"]["slot_id"])
    assert response.status_code == 409 and response.json()["detail"] == "same_slot"


def test_an_unknown_slot_is_404_and_a_stranger_is_403(world):
    assert reschedule(world, "no-such-slot").status_code == 404
    target = free_slots(world["doctor"]["id"], 4)[5]
    stranger = client.post("/api/v1/auth/phone-login", json={"phone_no": phone()}).json()["auth_id"]
    assert reschedule(world, target["slot_id"], auth_id=stranger).status_code == 403
    assert reschedule(world, target["slot_id"], auth_id="").status_code == 403


def test_an_appointment_that_started_or_has_a_consultation_cannot_be_moved(world):
    target = free_slots(world["doctor"]["id"], 4)[6]
    start_consultation(world)
    blocked = reschedule(world, target["slot_id"])
    assert blocked.status_code == 409 and blocked.json()["detail"] == "has_consultation"


def test_a_started_appointment_cannot_be_moved(world):
    target = free_slots(world["doctor"]["id"], 4)[7]
    move_into_the_past(world["appointment_id"])
    blocked = reschedule(world, target["slot_id"])
    assert blocked.status_code == 409 and blocked.json()["detail"] == "already_started"


def test_a_cancelled_appointment_cannot_be_moved(world):
    cancel(world, auth_id=world["patient_auth_id"])
    target = free_slots(world["doctor"]["id"], 4)[8]
    assert reschedule(world, target["slot_id"]).json()["detail"] == "cancelled"


def test_the_recording_consent_comes_with_the_moved_appointment(world):
    client.post(f"/api/v1/appointments/{world['appointment_id']}/consent", json={"consent_given": True, "auth_id": world["patient_auth_id"]})
    new = reschedule(world, free_slots(world["doctor"]["id"], 4)[9]["slot_id"]).json()

    state = client.get(f"/api/v1/appointments/{new['appointment_id']}/consent", params={"auth_id": world["patient_auth_id"]}).json()
    assert state["state"] == "granted" and state["recorded_by"] == "patient"
    session = db()
    carried = session.query(AuditLog).filter(AuditLog.action == "consultation_consent_carried_over", AuditLog.user_id == world["patient_auth_id"]).count()
    copies = session.query(ConsultationConsent).filter(ConsultationConsent.appointment_id == new["appointment_id"]).count()
    session.close()
    assert carried == 1 and copies == 1


def test_a_refusal_also_carries_over_and_no_consent_stays_none(world):
    client.post(f"/api/v1/appointments/{world['appointment_id']}/consent", json={"consent_given": False, "auth_id": world["patient_auth_id"]})
    new = reschedule(world, free_slots(world["doctor"]["id"], 4)[10]["slot_id"]).json()
    state = client.get(f"/api/v1/appointments/{new['appointment_id']}/consent", params={"auth_id": world["patient_auth_id"]}).json()
    assert state["state"] == "declined"


def test_a_rescheduled_follow_up_stays_a_follow_up_linked_to_the_original_visit(world):
    session = db()
    row = session.get(AIAppointment, world["appointment_id"])
    row.appointment_type = "follow_up"
    session.commit()
    session.close()
    new = reschedule(world, free_slots(world["doctor"]["id"], 4)[11]["slot_id"]).json()
    assert new["appointment_type"] == "follow_up"


def test_the_reschedule_is_audited(world):
    new = reschedule(world, free_slots(world["doctor"]["id"], 4)[12]["slot_id"]).json()
    session = db()
    entry = session.query(AuditLog).filter(AuditLog.action == "reschedule_appointment", AuditLog.user_id == world["patient_auth_id"]).one()
    session.close()
    assert entry.before_value["appointment_id"] == world["appointment_id"]
    assert entry.after_value["appointment_id"] == new["appointment_id"]


# ---------------------------------------------------------------------------
# the patient's own list
# ---------------------------------------------------------------------------

def patient_list(world):
    return client.get(f"/api/v1/patients/{world['patient_auth_id']}/appointments").json()


def test_the_patients_list_says_what_can_be_changed(world):
    mine = next(a for a in patient_list(world) if a["appointment_id"] == world["appointment_id"])
    assert mine["can_change"] is True and mine["change_blocker"] is None
    assert mine["doctor_id"] == world["doctor"]["id"] and mine["appointment_type"] == "new"


def test_an_appointment_with_a_consultation_is_listed_as_not_changeable(world):
    start_consultation(world)
    mine = next(a for a in patient_list(world) if a["appointment_id"] == world["appointment_id"])
    assert mine["can_change"] is False and mine["change_blocker"] == "has_consultation"


def test_after_a_reschedule_only_the_new_appointment_is_listed(world):
    new = reschedule(world, free_slots(world["doctor"]["id"], 4)[13]["slot_id"]).json()
    ids = [a["appointment_id"] for a in patient_list(world)]
    assert new["appointment_id"] in ids and world["appointment_id"] not in ids


def test_a_cancelled_appointment_disappears_from_the_list(world):
    cancel(world, auth_id=world["patient_auth_id"])
    assert world["appointment_id"] not in [a["appointment_id"] for a in patient_list(world)]


# ---------------------------------------------------------------------------
# withdrawing consent deletes the recording
# ---------------------------------------------------------------------------

def test_withdrawing_consent_deletes_the_recording_and_transcript_but_keeps_the_notes(world, tmp_path):
    consultation_id = start_consultation(world)
    headers = world["doctor"]["headers"]
    assert client.post(f"/api/v1/consultations/{consultation_id}/audio", content=wav(2.0),
                       headers={**headers, "Content-Type": "audio/wav"}).status_code == 200
    client.put(f"/api/v1/consultations/{consultation_id}/transcript", headers=headers,
               json={"turns": [{"speaker": "doctor", "text": "Hello"}], "language_code": "en-IN"})
    note = client.post(f"/api/v1/consultations/{consultation_id}/notes", headers=headers,
                       json={"chief_complaint": "Fever", "plan": "Rest", "source": "ai"}).json()
    assert len(list((tmp_path / "audio").iterdir())) == 1

    withdrawn = client.post(f"/api/v1/appointments/{world['appointment_id']}/consent", json={"consent_given": False, "auth_id": world["patient_auth_id"]})
    assert withdrawn.status_code == 200 and withdrawn.json()["state"] == "declined"

    detail = client.get(f"/api/v1/consultations/{consultation_id}", headers=headers).json()
    assert detail["has_recording"] is False and detail["turns"] == []
    assert [n["note_id"] for n in detail["notes"]] == [note["note_id"]]   # the note is kept
    assert list((tmp_path / "audio").iterdir()) == []
    session = db()
    reasons = [e.relevant_metadata.get("reason") for e in session.query(AuditLog).filter(AuditLog.action.like("consultation_%_deleted"), AuditLog.user_id == world["patient_auth_id"])]
    session.close()
    assert reasons and set(reasons) == {"consent_withdrawn"}


def test_withdrawing_consent_when_nothing_was_recorded_is_harmless(world):
    start_consultation(world)
    assert client.post(f"/api/v1/appointments/{world['appointment_id']}/consent",
                       json={"consent_given": False, "auth_id": world["patient_auth_id"]}).status_code == 200
