-- Migration 009: tie each appointment to a real doctor and the slot it locks.
--
-- All new columns are nullable / defaulted, so existing appointment rows
-- (which only have a free-text doctor_name) stay valid.
--
-- appointment_type and parent_appointment_id are used by the follow-up
-- booking added in a later stage ('new' vs 'follow_up').

ALTER TABLE ai_appointments
    ADD COLUMN IF NOT EXISTS doctor_id VARCHAR REFERENCES doctors(doctor_id),
    ADD COLUMN IF NOT EXISTS slot_id VARCHAR REFERENCES doctor_slots(slot_id),
    ADD COLUMN IF NOT EXISTS appointment_type VARCHAR NOT NULL DEFAULT 'new',
    ADD COLUMN IF NOT EXISTS parent_appointment_id VARCHAR REFERENCES ai_appointments(appointment_id);

CREATE INDEX IF NOT EXISTS idx_ai_appointments_doctor_id ON ai_appointments(doctor_id);
CREATE INDEX IF NOT EXISTS idx_ai_appointments_slot_id ON ai_appointments(slot_id);

-- Safety net behind the application-level slot lock: one slot can never have
-- two non-cancelled appointments.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_appointments_active_slot
    ON ai_appointments(slot_id)
    WHERE slot_id IS NOT NULL AND status <> 'cancelled';
