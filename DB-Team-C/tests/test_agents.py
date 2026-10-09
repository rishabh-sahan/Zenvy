"""The coordinator: events from the booking flows, routing to the doctor/patient agents, audit, history."""
from datetime import timedelta

import pytest

from app.models.agent import AgentAction, AgentEvent, AgentMessage
from app.services import agent_service
from helpers_stage3 import book, client, day_ist, db, free_slots, make_doctor, phone, quiet_world, world  # noqa: F401


def events(appointment_id, kind=None):
    session = db()
    try:
        rows = session.query(AgentEvent).filter(AgentEvent.appointment_id == appointment_id).order_by(AgentEvent.created_at).all()
        return [r for r in rows if kind is None or r.kind == kind]
    finally:
        session.close()


def messages_for(auth_id, kind=None):
    session = db()
    try:
        rows = session.query(AgentMessage).filter(AgentMessage.recipient_auth_id == auth_id).order_by(AgentMessage.created_at).all()
        return [r for r in rows if kind is None or r.kind == kind]
    finally:
        session.close()


def run_coordinator():
    session = db()
    try:
        return agent_service.process_all(session)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# events come from the real flows
# ---------------------------------------------------------------------------

def test_booking_queues_one_event(world):
    queued = events(world["appointment_id"], "appointment_booked")
    assert len(queued) == 1 and queued[0].status == "pending"
    assert queued[0].dedupe_key == f"booked:{world['appointment_id']}"


def test_the_same_fact_is_never_queued_twice(world):
    session = db()
    again = agent_service.emit(session, "appointment_booked", world["appointment_id"], dedupe_key=f"booked:{world['appointment_id']}")
    session.close()
    assert again is None and len(events(world["appointment_id"], "appointment_booked")) == 1


def test_the_doctor_agent_is_told_about_a_new_booking(world):
    run_coordinator()
    told = [m for m in messages_for(world["doctor"]["auth_id"], "appointment_booked") if m.appointment_id == world["appointment_id"]]
    assert len(told) == 1 and told[0].text.startswith("New appointment: Patient ••••")
    assert world["phone"][-4:] in told[0].text and world["phone"] not in told[0].text      # masked
    assert events(world["appointment_id"], "appointment_booked")[0].status == "done"


def test_a_cancellation_is_routed_to_the_doctor(world):
    assert client.post(f"/api/v1/appointments/{world['appointment_id']}/cancel", json={"auth_id": world["patient_auth_id"]}).status_code == 200
    assert len(events(world["appointment_id"], "appointment_cancelled")) == 1
    run_coordinator()
    told = [m for m in messages_for(world["doctor"]["auth_id"], "appointment_cancelled") if m.appointment_id == world["appointment_id"]]
    assert len(told) == 1 and told[0].text.startswith("Cancelled: Patient ••••")


def test_a_reschedule_is_routed_with_the_old_time(world):
    target = free_slots(world["doctor"]["id"], 5)[2]
    moved = client.post(f"/api/v1/appointments/{world['appointment_id']}/reschedule", json={"slot_id": target["slot_id"], "auth_id": world["patient_auth_id"]})
    assert moved.status_code == 200, moved.text
    new_id = moved.json()["appointment_id"]
    queued = events(new_id, "appointment_rescheduled")
    assert len(queued) == 1 and queued[0].payload["old_appointment_id"] == world["appointment_id"]
    run_coordinator()
    told = [m for m in messages_for(world["doctor"]["auth_id"], "appointment_rescheduled") if m.appointment_id == new_id]
    assert len(told) == 1 and "is now on" in told[0].text and "(was " in told[0].text


def test_a_follow_up_is_routed_to_both_agents(world):
    session = db()
    session.close()
    client.post(f"/api/v1/appointments/{world['appointment_id']}/consent", json={"consent_given": True, "auth_id": world["patient_auth_id"]})
    consultation = client.post("/api/v1/consultations", headers=world["doctor"]["headers"], json={"appointment_id": world["appointment_id"], "mode": "online"}).json()["consultation_id"]
    note = client.post(f"/api/v1/consultations/{consultation}/notes", headers=world["doctor"]["headers"],
                       json={"chief_complaint": "Fever", "plan": "Rest. Come back next week.", "source": "doctor_edit"}).json()
    approved = client.post(f"/api/v1/consultations/{consultation}/notes/{note['note_id']}/approve", headers=world["doctor"]["headers"]).json()
    follow_up_id = approved["follow_up"]["new_appointment_id"]
    assert follow_up_id and len(events(follow_up_id, "follow_up_booked")) == 1
    run_coordinator()
    doctor_told = [m for m in messages_for(world["doctor"]["auth_id"], "follow_up_booked") if m.appointment_id == follow_up_id]
    patient_told = [m for m in messages_for(world["patient_auth_id"], "follow_up_booked") if m.appointment_id == follow_up_id]
    assert len(doctor_told) == 1 and len(patient_told) == 1
    assert patient_told[0].text.startswith("Your follow-up with ")


# ---------------------------------------------------------------------------
# the coordinator itself
# ---------------------------------------------------------------------------

def test_processing_twice_handles_each_event_once(world):
    run_coordinator()
    second = run_coordinator()
    assert second.get("done", 0) == 0 or all(e.status == "done" for e in events(world["appointment_id"]))
    told = [m for m in messages_for(world["doctor"]["auth_id"], "appointment_booked") if m.appointment_id == world["appointment_id"]]
    assert len(told) == 1


def test_every_handled_event_is_in_the_audit_trail(world):
    run_coordinator()
    session = db()
    actions = session.query(AgentAction).filter(AgentAction.appointment_id == world["appointment_id"], AgentAction.agent == "coordinator").all()
    session.close()
    assert [a.tool for a in actions] == ["route:appointment_booked"] and actions[0].result == "ok"
    assert all(world["phone"] not in (a.summary or "") for a in actions)


def test_a_failing_handler_is_retried_then_marked_failed_without_stopping_the_others(world, monkeypatch):
    calls = {"n": 0}

    def boom(db_, event, now):
        calls["n"] += 1
        raise RuntimeError("handler exploded")

    monkeypatch.setitem(agent_service.HANDLERS, "appointment_booked", boom)
    for _ in range(agent_service.MAX_ATTEMPTS + 1):
        run_coordinator()
    event = events(world["appointment_id"], "appointment_booked")[0]
    assert event.status == "failed" and event.attempts == agent_service.MAX_ATTEMPTS
    assert "handler exploded" in event.last_error and calls["n"] >= agent_service.MAX_ATTEMPTS


def test_an_event_nobody_knows_how_to_handle_is_just_closed(world):
    session = db()
    event = agent_service.emit(session, "something_new", world["appointment_id"])
    session.close()
    run_coordinator()
    assert events(world["appointment_id"], "something_new")[0].status == "done"


def test_the_scheduler_runs_the_coordinator(world):
    from app.services.scheduler import Scheduler

    result = Scheduler(session_factory=lambda: db()).tick()
    assert "agents" in result and "missed" in result
    assert events(world["appointment_id"], "appointment_booked")[0].status == "done"


# ---------------------------------------------------------------------------
# the doctor's inbox
# ---------------------------------------------------------------------------

def test_the_doctor_reads_and_clears_their_updates(world):
    run_coordinator()
    mine = client.get("/api/v1/agent/messages", headers=world["doctor"]["headers"]).json()
    assert any(m["kind"] == "appointment_booked" and m["appointment_id"] == world["appointment_id"] for m in mine)
    assert client.post("/api/v1/agent/messages/read", headers=world["doctor"]["headers"], json={}).json()["marked"] >= 1
    assert client.get("/api/v1/agent/messages", headers=world["doctor"]["headers"]).json() == []
    everything = client.get("/api/v1/agent/messages", params={"unread": "false"}, headers=world["doctor"]["headers"]).json()
    assert any(m["read_at"] for m in everything)


def test_another_doctor_never_sees_these_updates(world):
    run_coordinator()
    other = client.get("/api/v1/agent/messages", headers=world["other"]["headers"]).json()
    assert all(m["appointment_id"] != world["appointment_id"] for m in other)
    assert client.get("/api/v1/agent/messages").status_code == 401


def test_a_patient_token_is_not_a_doctor_token(world):
    assert client.get("/api/v1/agent/messages", headers={"Authorization": "Bearer nonsense"}).status_code == 401


# ---------------------------------------------------------------------------
# what the doctor agent may look up
# ---------------------------------------------------------------------------

def _approved_visit(doctor, patient_auth_id, days_ahead, complaint, plan):
    appointment_id, _, _ = book(doctor["id"], patient_auth_id, slot_index=0, days_ahead=days_ahead)
    client.post(f"/api/v1/appointments/{appointment_id}/consent", json={"consent_given": True, "auth_id": patient_auth_id})
    consultation = client.post("/api/v1/consultations", headers=doctor["headers"], json={"appointment_id": appointment_id, "mode": "online"}).json()["consultation_id"]
    note = client.post(f"/api/v1/consultations/{consultation}/notes", headers=doctor["headers"],
                       json={"chief_complaint": complaint, "assessment": "Viral", "plan": plan, "source": "doctor_edit"}).json()
    client.post(f"/api/v1/consultations/{consultation}/notes/{note['note_id']}/approve", headers=doctor["headers"])
    client.put(f"/api/v1/consultations/{consultation}/prescription", headers=doctor["headers"],
               json={"items": [{"drug_name": "Paracetamol", "strength": "500 mg", "frequency_text": "1-0-1"}]})
    client.post(f"/api/v1/consultations/{consultation}/prescription/sign", headers=doctor["headers"])
    return appointment_id


def test_the_doctor_agent_sees_this_doctors_earlier_visits_with_the_patient(world):
    earlier = _approved_visit(world["doctor"], world["patient_auth_id"], 4, "Fever", "Rest.")
    later, _, _ = book(world["doctor"]["id"], world["patient_auth_id"], slot_index=3, days_ahead=6)
    history = client.get(f"/api/v1/appointments/{later}/patient-history", headers=world["doctor"]["headers"]).json()
    ids = [v["appointment_id"] for v in history]
    assert earlier in ids and later not in ids
    visit = next(v for v in history if v["appointment_id"] == earlier)
    assert visit["chief_complaint"] == "Fever" and visit["medicines"] == ["Paracetamol 500 mg"]


def test_the_history_never_includes_another_doctors_visits(world):
    _approved_visit(world["other"], world["patient_auth_id"], 4, "Cough", "Syrup.")
    later, _, _ = book(world["doctor"]["id"], world["patient_auth_id"], slot_index=3, days_ahead=6)
    history = client.get(f"/api/v1/appointments/{later}/patient-history", headers=world["doctor"]["headers"]).json()
    assert history == []


def test_the_history_of_someone_elses_appointment_is_refused(world):
    assert client.get(f"/api/v1/appointments/{world['appointment_id']}/patient-history", headers=world["other"]["headers"]).status_code == 403
    assert client.get("/api/v1/appointments/nope/patient-history", headers=world["doctor"]["headers"]).status_code == 404


# ---------------------------------------------------------------------------
# the audit endpoint used by the gateway agents
# ---------------------------------------------------------------------------

def test_a_doctor_agent_action_is_recorded_under_the_doctor(world):
    response = client.post("/api/v1/agents/actions", headers=world["doctor"]["headers"],
                           json={"agent": "doctor", "tool": "summarise_history", "summary": "3 visits", "appointment_id": world["appointment_id"]})
    assert response.status_code == 204
    session = db()
    row = session.query(AgentAction).filter(AgentAction.tool == "summarise_history", AgentAction.appointment_id == world["appointment_id"]).one()
    session.close()
    assert row.agent == "doctor" and row.actor_auth_id == world["doctor"]["auth_id"]


def test_a_doctor_agent_action_needs_a_doctor_token(world):
    assert client.post("/api/v1/agents/actions", json={"agent": "doctor", "tool": "x"}).status_code == 401


def test_a_patient_agent_action_is_recorded_for_a_known_patient_only(world):
    ok = client.post("/api/v1/agents/actions", json={"agent": "patient", "tool": "list_medicines", "auth_id": world["patient_auth_id"]})
    assert ok.status_code == 204
    assert client.post("/api/v1/agents/actions", json={"agent": "patient", "tool": "list_medicines", "auth_id": "nobody"}).status_code == 404


def test_the_coordinator_is_not_an_agent_the_gateway_can_impersonate(world):
    assert client.post("/api/v1/agents/actions", headers=world["doctor"]["headers"], json={"agent": "coordinator", "tool": "x"}).status_code == 422
