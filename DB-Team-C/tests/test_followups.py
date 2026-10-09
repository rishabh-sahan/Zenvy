"""Follow-up visits: spotting them in the plan, suggesting, booking on approval."""
from datetime import date, timedelta

import pytest

from app.models.ai_appointment import AIAppointment
from app.models.audit_log import AuditLog
from app.models.doctor_slot import DoctorSlot
from app.models.reminder import Reminder
from app.services.followup_service import detect_follow_up
from app.services.slot_service import as_ist
from helpers_stage3 import book, client, day_ist, db, free_slots, make_doctor, phone, quiet_world, wav  # noqa: F401

FRIDAY = date(2026, 10, 9)


# ---------------------------------------------------------------------------
# reading the plan
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("plan,days", [
    ("Take paracetamol 500 mg twice a day. Come back next week.", 7),
    ("Continue the medication and come back next week.", 7),
    ("Review in 10 days.", 10),
    ("Follow up after 2 weeks.", 14),
    ("Come back in a month.", 30),
    ("Return after three days.", 3),
    ("Please come back next week if the fever does not go away.", 7),
    ("Follow-up in a fortnight.", 14),
    ("Come back tomorrow.", 1),
    ("Review after a week.", 7),
    ("Revisit one month later.", 30),
    ("Review in about 5 days with the blood report.", 5),
    ("Rest and fluids. Return in 2 weeks for a check-up.", 14),
    ("COME BACK NEXT WEEK", 7),
    ("See me next Monday.", 3),                 # from a Friday
    ("Come back on Friday.", 7),                # the same weekday means next week
    ("Review in twelve days", 12),
])
def test_a_clear_follow_up_is_found(plan, days):
    found = detect_follow_up(plan, FRIDAY)
    assert found is not None, plan
    assert found["interval_days"] == days
    assert found["source_text"]


@pytest.mark.parametrize("plan", [
    "",
    "Take paracetamol for 5 days.",                       # how long to take a medicine, not a visit
    "Take the tablets for two weeks and drink water.",
    "Come back if it gets worse.",                       # no time given
    "Return if symptoms persist.",
    "No follow-up needed.",
    "No need to come back.",
    "No further review required.",
    "Drink plenty of fluids.",
    "Review in 400 days.",                               # absurd
    "Take medicine for 3 days and review if symptoms persist.",
])
def test_things_that_are_not_a_follow_up_are_left_alone(plan):
    assert detect_follow_up(plan, FRIDAY) is None, plan


def test_only_the_first_clear_follow_up_is_used():
    found = detect_follow_up("Review in 3 days. Then come back next month.", FRIDAY)
    assert found["interval_days"] == 3


def test_the_sentence_it_came_from_is_kept_for_the_doctor_to_read():
    found = detect_follow_up("Rest well. Please come back next week if the fever does not go away. Drink water.", FRIDAY)
    assert found["source_text"] == "Please come back next week if the fever does not go away."


# ---------------------------------------------------------------------------
# a consultation with a note
# ---------------------------------------------------------------------------

@pytest.fixture
def visit():
    """A booked visit (2 days from now, 08:00 IST) with consent and a started consultation."""
    session = db()
    doctor = make_doctor(session, "Owner")
    other = make_doctor(session, "Other")
    session.close()
    patient_phone = phone()
    patient = client.post("/api/v1/auth/phone-login", json={"phone_no": patient_phone}).json()
    appointment_id, session_id, slot = book(doctor["id"], patient["auth_id"], slot_index=0, days_ahead=2)
    client.post(f"/api/v1/appointments/{appointment_id}/consent", json={"consent_given": True, "auth_id": patient["auth_id"]})
    consultation = client.post("/api/v1/consultations", headers=doctor["headers"], json={"appointment_id": appointment_id, "mode": "online"}).json()
    return {
        "doctor": doctor, "other": other, "appointment_id": appointment_id, "session_id": session_id,
        "patient_auth_id": patient["auth_id"], "phone": patient_phone, "consultation_id": consultation["consultation_id"],
        "visit_day": day_ist(2),
    }


def write_note(visit, plan, source="ai"):
    response = client.post(
        f"/api/v1/consultations/{visit['consultation_id']}/notes", headers=visit["doctor"]["headers"],
        json={"chief_complaint": "Fever", "plan": plan, "source": source},
    )
    assert response.status_code == 201, response.text
    return response.json()


def follow_up(visit, headers=None):
    return client.get(f"/api/v1/consultations/{visit['consultation_id']}/follow-up", headers=headers or visit["doctor"]["headers"])


def approve(visit, note):
    return client.post(
        f"/api/v1/consultations/{visit['consultation_id']}/notes/{note['note_id']}/approve", headers=visit["doctor"]["headers"]
    )


# ---------------------------------------------------------------------------
# the suggestion
# ---------------------------------------------------------------------------

def test_a_follow_up_in_the_plan_becomes_a_suggestion_for_the_same_time_a_week_later(visit):
    write_note(visit, "Paracetamol twice a day. Come back next week.")
    body = follow_up(visit).json()
    assert body["status"] == "suggested" and body["interval_days"] == 7
    assert body["suggested_date"] == (visit["visit_day"] + timedelta(days=7)).isoformat()
    assert body["suggested_time"] == "08:00"
    assert body["source_text"] == "Come back next week."
    assert body["edited_by_doctor"] is False and body["new_appointment_id"] is None
    # what a booking would take right now
    assert as_ist_str(body["preview_datetime"]) == (visit["visit_day"] + timedelta(days=7)).isoformat() + "T08:00"


def as_ist_str(value):
    return value[:16]


def test_a_plan_without_a_follow_up_suggests_nothing(visit):
    write_note(visit, "Take paracetamol for 5 days. Come back if it gets worse.")
    assert follow_up(visit).json() is None


def test_the_suggestion_follows_the_plan_as_the_note_is_edited(visit):
    write_note(visit, "Come back next week.")
    write_note(visit, "Review in 10 days.", source="doctor_edit")
    assert follow_up(visit).json()["interval_days"] == 10
    write_note(visit, "Rest well.", source="doctor_edit")
    assert follow_up(visit).json() is None               # removed from the plan: the suggestion goes


def test_a_date_the_doctor_chose_is_not_overwritten_by_later_edits(visit):
    write_note(visit, "Come back next week.")
    chosen = (visit["visit_day"] + timedelta(days=5)).isoformat()
    changed = client.put(f"/api/v1/consultations/{visit['consultation_id']}/follow-up",
                         headers=visit["doctor"]["headers"], json={"date": chosen, "time": "10:30"})
    assert changed.status_code == 200
    assert changed.json()["suggested_date"] == chosen and changed.json()["suggested_time"] == "10:30"
    assert changed.json()["edited_by_doctor"] is True and changed.json()["interval_days"] == 5

    write_note(visit, "Review in 14 days.", source="doctor_edit")
    assert follow_up(visit).json()["suggested_date"] == chosen


def test_the_doctor_can_remove_the_follow_up_and_it_stays_removed(visit):
    write_note(visit, "Come back next week.")
    removed = client.delete(f"/api/v1/consultations/{visit['consultation_id']}/follow-up", headers=visit["doctor"]["headers"])
    assert removed.status_code == 200 and removed.json()["status"] == "declined"
    write_note(visit, "Come back next week. Drink water.", source="doctor_edit")     # same follow-up again
    assert follow_up(visit).json()["status"] == "declined"
    write_note(visit, "Review in 3 days.", source="doctor_edit")                      # a different one: offered again
    again = follow_up(visit).json()
    assert again["status"] == "suggested" and again["interval_days"] == 3


def test_a_chosen_date_must_be_after_the_visit(visit):
    response = client.put(f"/api/v1/consultations/{visit['consultation_id']}/follow-up", headers=visit["doctor"]["headers"],
                          json={"date": visit["visit_day"].isoformat()})
    assert response.status_code == 409 and response.json()["detail"] == "must_be_after_the_visit"


def test_only_the_treating_doctor_can_touch_the_follow_up(visit):
    write_note(visit, "Come back next week.")
    url = f"/api/v1/consultations/{visit['consultation_id']}/follow-up"
    for headers in (visit["other"]["headers"],):
        assert client.get(url, headers=headers).status_code == 403
        assert client.put(url, headers=headers, json={"date": (visit["visit_day"] + timedelta(days=3)).isoformat()}).status_code == 403
        assert client.delete(url, headers=headers).status_code == 403
        assert client.post(url + "/book", headers=headers).status_code == 403
    assert client.get(url).status_code == 401


# ---------------------------------------------------------------------------
# booking it
# ---------------------------------------------------------------------------

def test_approving_the_note_books_the_follow_up_in_one_go(visit):
    note = write_note(visit, "Continue the medication. Come back next week.")
    approved = approve(visit, note)
    assert approved.status_code == 200
    result = approved.json()["follow_up"]
    assert result["status"] == "booked" and result["new_appointment_id"]
    assert result["new_appointment_datetime"].startswith((visit["visit_day"] + timedelta(days=7)).isoformat() + "T08:00")

    session = db()
    new = session.get(AIAppointment, result["new_appointment_id"])
    original = session.get(AIAppointment, visit["appointment_id"])
    assert new.appointment_type == "follow_up" and new.parent_appointment_id == original.appointment_id
    assert new.doctor_id == original.doctor_id and new.status.value == "confirmed"
    assert new.patient_phone_no == original.patient_phone_no          # the same patient
    assert session.get(DoctorSlot, new.slot_id).status == "booked"     # and it is locked
    kinds = {(r.recipient_type, r.kind) for r in session.query(Reminder).filter(Reminder.appointment_id == new.appointment_id)}
    audit = session.query(AuditLog).filter(AuditLog.action == "follow_up_booked", AuditLog.user_id == visit["patient_auth_id"]).count()
    session.close()
    assert {("patient", "follow_up_booked"), ("patient", "reminder_24h"), ("patient", "reminder_2h"),
            ("doctor", "booked"), ("doctor", "reminder_24h"), ("doctor", "reminder_2h")} <= kinds
    assert audit == 1


def test_the_follow_up_shows_up_in_the_patients_and_the_doctors_lists_as_a_follow_up(visit):
    approve(visit, write_note(visit, "Come back next week."))
    mine = client.get(f"/api/v1/patients/{visit['patient_auth_id']}/appointments").json()
    assert [a["appointment_type"] for a in mine].count("follow_up") == 1
    theirs = client.get("/api/v1/doctor/appointments", headers=visit["doctor"]["headers"]).json()
    assert [a["appointment_type"] for a in theirs].count("follow_up") == 1


def test_the_original_appointments_history_points_to_the_follow_up(visit):
    result = approve(visit, write_note(visit, "Come back next week.")).json()["follow_up"]
    history = client.get(f"/api/v1/appointments/{visit['appointment_id']}/history", headers=visit["doctor"]["headers"]).json()
    assert history["follow_up_appointment_id"] == result["new_appointment_id"]
    assert any("follow-up booked" in e["detail"] for e in history["events"])


def test_an_approval_without_a_follow_up_books_nothing(visit):
    approved = approve(visit, write_note(visit, "Rest well and drink water."))
    assert approved.status_code == 200 and approved.json()["follow_up"] is None


def test_a_removed_follow_up_is_not_booked(visit):
    note = write_note(visit, "Come back next week.")
    client.delete(f"/api/v1/consultations/{visit['consultation_id']}/follow-up", headers=visit["doctor"]["headers"])
    approved = approve(visit, note).json()
    assert approved["status"] == "approved" and approved["follow_up"]["status"] == "declined"
    session = db()
    assert session.query(AIAppointment).filter(AIAppointment.parent_appointment_id == visit["appointment_id"]).count() == 0
    session.close()


def test_the_doctors_own_date_is_the_one_booked(visit):
    note = write_note(visit, "Come back next week.")
    chosen = visit["visit_day"] + timedelta(days=4)
    client.put(f"/api/v1/consultations/{visit['consultation_id']}/follow-up", headers=visit["doctor"]["headers"],
               json={"date": chosen.isoformat(), "time": "11:30"})
    result = approve(visit, note).json()["follow_up"]
    assert result["new_appointment_datetime"].startswith(chosen.isoformat() + "T11:30")


def fill_the_days(doctor_id, first_day, count, leave_free=()):
    """Mark every slot of the doctor as booked on `count` days from `first_day`, except those in leave_free."""
    for offset in range(count):
        day = first_day + timedelta(days=offset)
        client.get(f"/api/v1/doctors/{doctor_id}/slots", params={"date": day.isoformat()})   # makes the slots exist
    session = db()
    for slot in session.query(DoctorSlot).filter(DoctorSlot.doctor_id == doctor_id):
        local = as_ist(slot.slot_start)
        if first_day <= local.date() < first_day + timedelta(days=count) and slot.slot_id not in leave_free:
            slot.status = "booked"
    session.commit()
    session.close()


def test_if_the_exact_time_is_taken_the_nearest_free_time_that_day_is_used(visit):
    target = visit["visit_day"] + timedelta(days=7)
    wanted = next(s for s in free_slots(visit["doctor"]["id"], 9) if s["slot_start"].endswith("08:00:00+05:30"))
    neighbour = next(s for s in free_slots(visit["doctor"]["id"], 9) if s["slot_start"].endswith("09:00:00+05:30"))
    fill_the_days(visit["doctor"]["id"], target, 1, leave_free={neighbour["slot_id"]})
    assert wanted["slot_id"] != neighbour["slot_id"]

    result = approve(visit, write_note(visit, "Come back next week.")).json()["follow_up"]
    assert result["status"] == "booked"
    assert result["new_appointment_datetime"].startswith(target.isoformat() + "T09:00")


def test_if_the_day_is_full_the_next_day_with_a_free_slot_is_used(visit):
    target = visit["visit_day"] + timedelta(days=7)
    fill_the_days(visit["doctor"]["id"], target, 2)
    result = approve(visit, write_note(visit, "Come back next week.")).json()["follow_up"]
    assert result["status"] == "booked"
    assert result["new_appointment_datetime"].startswith((target + timedelta(days=2)).isoformat())


def test_with_no_free_slot_at_all_the_approval_still_works_and_says_why(visit):
    target = visit["visit_day"] + timedelta(days=7)
    fill_the_days(visit["doctor"]["id"], target, 9)
    note = write_note(visit, "Come back next week.")
    approved = approve(visit, note)
    assert approved.status_code == 200 and approved.json()["status"] == "approved"      # the note IS approved
    result = approved.json()["follow_up"]
    assert result["status"] == "failed" and result["failure_reason"] == "no_free_slot"

    retry = client.post(f"/api/v1/consultations/{visit['consultation_id']}/follow-up/book", headers=visit["doctor"]["headers"])
    assert retry.status_code == 409 and retry.json()["detail"] == "no_free_slot"


def test_after_a_failure_the_doctor_can_pick_another_date_and_book_it(visit):
    target = visit["visit_day"] + timedelta(days=7)
    fill_the_days(visit["doctor"]["id"], target, 9)
    approve(visit, write_note(visit, "Come back next week."))

    elsewhere = visit["visit_day"] + timedelta(days=3)
    client.put(f"/api/v1/consultations/{visit['consultation_id']}/follow-up", headers=visit["doctor"]["headers"],
               json={"date": elsewhere.isoformat(), "time": "08:00"})
    booked = client.post(f"/api/v1/consultations/{visit['consultation_id']}/follow-up/book", headers=visit["doctor"]["headers"])
    assert booked.status_code == 200 and booked.json()["status"] == "booked"
    assert booked.json()["new_appointment_datetime"].startswith(elsewhere.isoformat())


def test_a_booked_follow_up_cannot_be_booked_again_changed_or_removed(visit):
    approve(visit, write_note(visit, "Come back next week."))
    url = f"/api/v1/consultations/{visit['consultation_id']}/follow-up"
    headers = visit["doctor"]["headers"]
    assert client.post(url + "/book", headers=headers).json()["detail"] == "nothing_to_book"
    assert client.delete(url, headers=headers).json()["detail"] == "already_booked"
    assert client.put(url, headers=headers, json={"date": (visit["visit_day"] + timedelta(days=3)).isoformat()}).json()["detail"] == "already_booked"
    # editing the note later does not touch a booked follow-up
    write_note(visit, "Review in 3 days.", source="doctor_edit")
    assert client.get(url, headers=headers).json()["status"] == "booked"


def test_approving_a_second_version_does_not_book_a_second_follow_up(visit):
    approve(visit, write_note(visit, "Come back next week."))
    second = approve(visit, write_note(visit, "Come back next week. Also drink water.", source="doctor_edit")).json()
    assert second["follow_up"]["status"] == "booked"
    session = db()
    assert session.query(AIAppointment).filter(AIAppointment.parent_appointment_id == visit["appointment_id"]).count() == 1
    session.close()


def test_the_follow_up_can_itself_be_rescheduled_or_cancelled_by_the_patient(visit):
    result = approve(visit, write_note(visit, "Come back next week.")).json()["follow_up"]
    new_id = result["new_appointment_id"]
    target = free_slots(visit["doctor"]["id"], 11)[5]
    moved = client.post(f"/api/v1/appointments/{new_id}/reschedule", json={"slot_id": target["slot_id"], "auth_id": visit["patient_auth_id"]})
    assert moved.status_code == 200 and moved.json()["appointment_type"] == "follow_up"
    assert moved.json()["parent_appointment_id"] == visit["appointment_id"]
    cancelled = client.post(f"/api/v1/appointments/{moved.json()['appointment_id']}/cancel", json={"auth_id": visit["patient_auth_id"]})
    assert cancelled.status_code == 200
