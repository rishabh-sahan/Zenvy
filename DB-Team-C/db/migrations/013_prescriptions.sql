-- Migration 013: medication handling.
--
--   prescriptions       one per version, per consultation: draft -> signed (locked) -> superseded
--   prescription_items  the medicines of one version
--   medication_doses    one row per scheduled dose; the patient marks it taken
--   reminders           + dose_id, and the new kind 'medication' (one reminder per dose)

CREATE TABLE IF NOT EXISTS prescriptions (
    prescription_id VARCHAR PRIMARY KEY,
    consultation_id VARCHAR NOT NULL REFERENCES consultations(consultation_id) ON DELETE CASCADE,
    appointment_id VARCHAR NOT NULL REFERENCES ai_appointments(appointment_id),
    doctor_id VARCHAR NOT NULL REFERENCES doctors(doctor_id),
    patient_auth_id VARCHAR REFERENCES authentication(auth_id),
    version INTEGER NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'signed', 'superseded')),
    source VARCHAR NOT NULL DEFAULT 'doctor_edit'
        CHECK (source IN ('transcript', 'doctor_edit', 'carried_forward')),
    created_by_auth_id VARCHAR REFERENCES authentication(auth_id),
    signed_by_auth_id VARCHAR REFERENCES authentication(auth_id),
    signed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_prescriptions_version UNIQUE (consultation_id, version)
);

CREATE INDEX IF NOT EXISTS idx_prescriptions_patient ON prescriptions(patient_auth_id, status);

CREATE TABLE IF NOT EXISTS prescription_items (
    item_id VARCHAR PRIMARY KEY,
    prescription_id VARCHAR NOT NULL REFERENCES prescriptions(prescription_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    drug_name VARCHAR NOT NULL,
    strength VARCHAR,
    form VARCHAR,
    dose_text VARCHAR,
    frequency_text VARCHAR,
    dose_times JSONB NOT NULL DEFAULT '[]'::jsonb,
    food VARCHAR NOT NULL DEFAULT 'any' CHECK (food IN ('before', 'after', 'with', 'any')),
    duration_days INTEGER CHECK (duration_days IS NULL OR duration_days BETWEEN 1 AND 90),
    as_needed BOOLEAN NOT NULL DEFAULT FALSE,
    instructions TEXT,
    from_transcript BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_prescription_items_prescription ON prescription_items(prescription_id);

CREATE TABLE IF NOT EXISTS medication_doses (
    dose_id VARCHAR PRIMARY KEY,
    prescription_id VARCHAR NOT NULL REFERENCES prescriptions(prescription_id) ON DELETE CASCADE,
    item_id VARCHAR NOT NULL REFERENCES prescription_items(item_id) ON DELETE CASCADE,
    patient_auth_id VARCHAR REFERENCES authentication(auth_id),
    appointment_id VARCHAR NOT NULL REFERENCES ai_appointments(appointment_id),
    due_at TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'taken', 'missed', 'cancelled')),
    taken_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_medication_doses_slot UNIQUE (item_id, due_at)
);

CREATE INDEX IF NOT EXISTS idx_medication_doses_patient ON medication_doses(patient_auth_id, status, due_at);
CREATE INDEX IF NOT EXISTS idx_medication_doses_due ON medication_doses(status, due_at);

-- One reminder per dose: the "once per (appointment, person, kind)" rule now includes the dose.
ALTER TABLE reminders ADD COLUMN IF NOT EXISTS dose_id VARCHAR NOT NULL DEFAULT '';
ALTER TABLE reminders DROP CONSTRAINT IF EXISTS reminders_kind_check;
ALTER TABLE reminders ADD CONSTRAINT reminders_kind_check
    CHECK (kind IN ('booked', 'reminder_24h', 'reminder_2h', 'follow_up_booked', 'cancelled', 'rescheduled', 'medication'));
ALTER TABLE reminders DROP CONSTRAINT IF EXISTS uq_reminders_once;
ALTER TABLE reminders ADD CONSTRAINT uq_reminders_once UNIQUE (appointment_id, recipient_type, kind, dose_id);
