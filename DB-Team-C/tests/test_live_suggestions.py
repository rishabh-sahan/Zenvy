"""Live Assist storage: cards for the doctor during a consultation, and what happens to them."""
from datetime import timedelta

import pytest

from app.models.live_suggestion import LiveSuggestion
from app.services import consultation_service
from app.services.slot_service import utcnow
from helpers_stage3 import book, client, db, make_doctor, phone, quiet_world, wav  # noqa: F401


@pytest.fixture
def visit():
    session = db()
    doctor = make_doctor(session, "Owner")
    other = make_doctor(session, "Other")
    session.close()
    patient_phone = phone()
    patient = client.post("/api/v1/auth/phone-login", json={"phone_no": patient_phone}).json()
    appointment_id, _, _ = book(doctor["id"], patient["auth_id"], slot_index=0, days_ahead=2)
    client.post(f"/api/v1/appointments/{appointment_id}/consent", json={"consent_given": True, "auth_id": patient["auth_id"]})
    consultation = client.post(
        "/api/v1/consultations", headers=doctor["headers"], json={"appointment_id": appointment_id, "mode": "in_person"}
    ).json()
    return {"doctor": doctor, "other": other, "appointment_id": appointment_id, "phone": patient_phone,
            "patient_auth_id": patient["auth_id"], "consultation_id": consultation["consultation_id"]}


def url(visit, suffix=""):
    return f"/api/v1/consultations/{visit['consultation_id']}/live/suggestions{suffix}"


CARD = {"kind": "ask", "text": "Ask about allergies.", "evidence": "I have a rash"}


def add(visit, cards, headers=None):
    return client.post(url(visit), headers=headers or visit["doctor"]["headers"], json={"cards": cards})


# ---------------------------------------------------------------------------

def test_cards_are_stored_in_order_and_read_back(visit):
    first = add(visit, [CARD, {"kind": "red_flag", "text": "Chest pain mentioned.", "source": "rule"}])
    assert first.status_code == 201
    body = first.json()
    assert [c["seq"] for c in body] == [1, 2] and [c["kind"] for c in body] == ["ask", "red_flag"]
    assert body[0]["status"] == "shown" and body[1]["source"] == "rule" and body[0]["source"] == "model"
    second = add(visit, [{"kind": "wrap_up", "text": "Follow-up not discussed."}]).json()
    assert second[0]["seq"] == 3                                   # numbering continues

    everything = client.get(url(visit), headers=visit["doctor"]["headers"]).json()
    assert [c["seq"] for c in everything] == [1, 2, 3]
    newer = client.get(url(visit), params={"after": 2}, headers=visit["doctor"]["headers"]).json()
    assert [c["seq"] for c in newer] == [3]


@pytest.mark.parametrize("card", [
    {"kind": "diagnosis", "text": "x"},          # not a known kind
    {"kind": "ask", "text": ""},
    {"kind": "ask", "text": "   "},
    {"kind": "ask"},
    {"kind": "ask", "text": "y" * 401},
])
def test_bad_cards_are_refused(visit, card):
    assert add(visit, [card]).status_code == 422


def test_at_most_ten_cards_in_one_call(visit):
    assert add(visit, [CARD] * 11).status_code == 422
    assert add(visit, []).status_code == 422


def test_a_consultation_cannot_hold_an_unbounded_number_of_cards(visit):
    for _ in range(30):
        assert add(visit, [CARD] * 10).status_code == 201
    over = add(visit, [CARD])
    assert over.status_code == 422 and "Too many" in over.text


def test_only_the_treating_doctor_can_read_write_or_act(visit):
    first = add(visit, [CARD]).json()[0]
    other = visit["other"]["headers"]
    assert add(visit, [CARD], headers=other).status_code == 403
    assert client.get(url(visit), headers=other).status_code == 403
    assert client.patch(url(visit, f"/{first['suggestion_id']}"), headers=other, json={"status": "dismissed"}).status_code == 403
    assert client.get(url(visit)).status_code == 401
    assert client.post(url(visit), json={"cards": [CARD]}).status_code == 401


def test_a_consultation_that_does_not_exist_is_404(visit):
    response = client.get("/api/v1/consultations/nope/live/suggestions", headers=visit["doctor"]["headers"])
    assert response.status_code == 404


def test_the_doctor_records_what_they_did_with_a_card(visit):
    card = add(visit, [CARD]).json()[0]
    for status in ("dismissed", "pinned", "added", "asked"):
        response = client.patch(url(visit, f"/{card['suggestion_id']}"), headers=visit["doctor"]["headers"], json={"status": status})
        assert response.status_code == 200 and response.json()["status"] == status
    session = db()
    row = session.get(LiveSuggestion, card["suggestion_id"])
    session.close()
    assert row.acted_at is not None


def test_a_status_the_doctor_cannot_choose_is_refused(visit):
    card = add(visit, [CARD]).json()[0]
    for status in ("shown", "deleted", ""):
        assert client.patch(url(visit, f"/{card['suggestion_id']}"), headers=visit["doctor"]["headers"], json={"status": status}).status_code == 422
    assert client.patch(url(visit, "/does-not-exist"), headers=visit["doctor"]["headers"], json={"status": "dismissed"}).status_code == 404


def test_nothing_is_stored_once_consent_is_withdrawn(visit):
    add(visit, [CARD])
    client.post(f"/api/v1/appointments/{visit['appointment_id']}/consent", json={"consent_given": False, "auth_id": visit["patient_auth_id"]})
    again = add(visit, [CARD])
    assert again.status_code == 409 and again.json()["detail"] == "consent_required"


def test_withdrawing_consent_deletes_the_cards_even_with_no_recording(visit):
    add(visit, [CARD, CARD])
    assert len(client.get(url(visit), headers=visit["doctor"]["headers"]).json()) == 2
    client.post(f"/api/v1/appointments/{visit['appointment_id']}/consent", json={"consent_given": False, "auth_id": visit["patient_auth_id"]})
    session = db()
    left = session.query(LiveSuggestion).filter(LiveSuggestion.consultation_id == visit["consultation_id"]).count()
    session.close()
    assert left == 0


def test_deleting_the_recording_deletes_the_cards(visit):
    add(visit, [CARD])
    response = client.delete(f"/api/v1/consultations/{visit['consultation_id']}/recording", headers=visit["doctor"]["headers"])
    assert response.status_code == 200
    assert client.get(url(visit), headers=visit["doctor"]["headers"]).json() == []


def test_the_retention_purge_removes_old_cards_and_keeps_recent_ones(visit):
    add(visit, [CARD])
    session = db()
    consultation_service.purge_expired(session, utcnow() + timedelta(days=30))     # inside the 90-day window
    still = session.query(LiveSuggestion).filter(LiveSuggestion.consultation_id == visit["consultation_id"]).count()
    consultation_service.purge_expired(session, utcnow() + timedelta(days=120))    # past it
    gone = session.query(LiveSuggestion).filter(LiveSuggestion.consultation_id == visit["consultation_id"]).count()
    session.close()
    assert still == 1 and gone == 0


def test_the_purge_result_keeps_its_shape(visit):
    session = db()
    assert set(consultation_service.purge_expired(session, utcnow())) == {"audio", "transcripts"}
    session.close()


def test_cards_never_carry_a_phone_number(visit):
    add(visit, [CARD])
    text = client.get(url(visit), headers=visit["doctor"]["headers"]).text
    assert visit["phone"] not in text and visit["patient_auth_id"] not in text


def test_other_consultations_cards_are_separate(visit):
    add(visit, [CARD])
    session = db()
    other_doctor = make_doctor(session, "Third")
    session.close()
    patient = client.post("/api/v1/auth/phone-login", json={"phone_no": phone()}).json()
    appointment_id, _, _ = book(other_doctor["id"], patient["auth_id"], slot_index=1, days_ahead=2)
    client.post(f"/api/v1/appointments/{appointment_id}/consent", json={"consent_given": True, "auth_id": patient["auth_id"]})
    consultation = client.post("/api/v1/consultations", headers=other_doctor["headers"], json={"appointment_id": appointment_id, "mode": "online"}).json()
    mine = client.get(f"/api/v1/consultations/{consultation['consultation_id']}/live/suggestions", headers=other_doctor["headers"]).json()
    assert mine == []
