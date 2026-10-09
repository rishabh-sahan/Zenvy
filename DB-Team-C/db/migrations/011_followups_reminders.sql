-- Migration 011: follow-up visits, reminders/notices, and cancel / reschedule details.
--
--   ai_appointments  + cancelled_at, cancel_reason, cancelled_by, rescheduled_from_id
--   follow_ups       one per consultation: suggested from the note's plan, booked on approval
--   reminders        every message about an appointment (reminders, confirmations, notices),
--                    to the patient or the doctor. No phone numbers are stored here.

ALTER TABLE ai_appointments
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cancel_reason VARCHAR,
    ADD COLUMN IF NOT EXISTS cancelled_by VARCHAR,
    ADD COLUMN IF NOT EXISTS rescheduled_from_id VARCHAR REFERENCES ai_appointments(appointment_id);

CREATE INDEX IF NOT EXISTS idx_ai_appointments_rescheduled_from ON ai_appointments(rescheduled_from_id);

CREATE TABLE IF NOT EXISTS follow_ups (
    follow_up_id VARCHAR PRIMARY KEY,
    consultation_id VARCHAR NOT NULL UNIQUE REFERENCES consultations(consultation_id) ON DELETE CASCADE,
    appointment_id VARCHAR NOT NULL REFERENCES ai_appointments(appointment_id),
    doctor_id VARCHAR NOT NULL REFERENCES doctors(doctor_id),
    status VARCHAR NOT NULL DEFAULT 'suggested'
        CHECK (status IN ('suggested', 'booked', 'declined', 'failed')),
    interval_days INTEGER CHECK (interval_days IS NULL OR interval_days BETWEEN 1 AND 365),
    source_text TEXT,
    suggested_date DATE,
    suggested_time TIME,
    edited_by_doctor BOOLEAN NOT NULL DEFAULT FALSE,
    new_appointment_id VARCHAR REFERENCES ai_appointments(appointment_id),
    failure_reason VARCHAR,
    decided_by_auth_id VARCHAR REFERENCES authentication(auth_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_follow_ups_appointment_id ON follow_ups(appointment_id);
CREATE INDEX IF NOT EXISTS idx_follow_ups_doctor_id ON follow_ups(doctor_id);

CREATE TABLE IF NOT EXISTS reminders (
    reminder_id VARCHAR PRIMARY KEY,
    appointment_id VARCHAR NOT NULL REFERENCES ai_appointments(appointment_id),
    recipient_type VARCHAR NOT NULL CHECK (recipient_type IN ('patient', 'doctor')),
    recipient_auth_id VARCHAR REFERENCES authentication(auth_id),
    kind VARCHAR NOT NULL
        CHECK (kind IN ('booked', 'reminder_24h', 'reminder_2h', 'follow_up_booked', 'cancelled', 'rescheduled')),
    send_at TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'cancelled', 'skipped')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error VARCHAR,
    details JSONB,
    message_text TEXT,
    mode VARCHAR CHECK (mode IS NULL OR mode IN ('mock', 'live')),
    provider_message_id VARCHAR,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_reminders_once UNIQUE (appointment_id, recipient_type, kind)
);

CREATE INDEX IF NOT EXISTS idx_reminders_appointment_id ON reminders(appointment_id);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, send_at);
