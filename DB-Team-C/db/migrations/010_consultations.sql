-- Migration 010: consultation recording and the AI scribe.
--
--   consultation_consents  - who agreed (or refused) to being recorded
--   consultations          - one per appointment; points at the ENCRYPTED audio file
--   consultation_turns     - the transcript as labelled turns (doctor / patient)
--   consultation_notes     - versions of the structured English note
--
-- Patients are identified by authentication.auth_id, never by phone number.
-- Audio is stored on disk, encrypted; only its path and checksum live here.

CREATE TABLE IF NOT EXISTS consultation_consents (
    consent_id VARCHAR PRIMARY KEY,
    appointment_id VARCHAR NOT NULL REFERENCES ai_appointments(appointment_id),
    patient_auth_id VARCHAR REFERENCES authentication(auth_id),
    consent_given BOOLEAN NOT NULL,
    recorded_by VARCHAR NOT NULL CHECK (recorded_by IN ('patient', 'doctor_on_behalf')),
    recorded_by_auth_id VARCHAR REFERENCES authentication(auth_id),
    message_version VARCHAR NOT NULL,
    language VARCHAR NOT NULL DEFAULT 'en',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_consultation_consents_appointment_id ON consultation_consents(appointment_id);
CREATE INDEX IF NOT EXISTS idx_consultation_consents_patient_auth_id ON consultation_consents(patient_auth_id);

CREATE TABLE IF NOT EXISTS consultations (
    consultation_id VARCHAR PRIMARY KEY,
    appointment_id VARCHAR NOT NULL UNIQUE REFERENCES ai_appointments(appointment_id),
    doctor_id VARCHAR NOT NULL REFERENCES doctors(doctor_id),
    patient_auth_id VARCHAR REFERENCES authentication(auth_id),
    mode VARCHAR NOT NULL DEFAULT 'in_person' CHECK (mode IN ('online', 'phone', 'in_person')),
    status VARCHAR NOT NULL DEFAULT 'created',
    failure_reason VARCHAR,
    audio_path VARCHAR,
    audio_bytes INTEGER,
    audio_seconds DOUBLE PRECISION,
    audio_sha256 VARCHAR,
    audio_uploaded_at TIMESTAMPTZ,
    audio_deleted_at TIMESTAMPTZ,
    language_code VARCHAR,
    transcript_deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_consultations_doctor_id ON consultations(doctor_id);
CREATE INDEX IF NOT EXISTS idx_consultations_patient_auth_id ON consultations(patient_auth_id);
CREATE INDEX IF NOT EXISTS idx_consultations_status ON consultations(status);

CREATE TABLE IF NOT EXISTS consultation_turns (
    turn_id VARCHAR PRIMARY KEY,
    consultation_id VARCHAR NOT NULL REFERENCES consultations(consultation_id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    speaker VARCHAR NOT NULL DEFAULT 'unknown' CHECK (speaker IN ('doctor', 'patient', 'unknown')),
    text TEXT NOT NULL,
    start_seconds DOUBLE PRECISION,
    end_seconds DOUBLE PRECISION,
    language_code VARCHAR,
    edited BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_consultation_turns_seq UNIQUE (consultation_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_consultation_turns_consultation_id ON consultation_turns(consultation_id);

CREATE TABLE IF NOT EXISTS consultation_notes (
    note_id VARCHAR PRIMARY KEY,
    consultation_id VARCHAR NOT NULL REFERENCES consultations(consultation_id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved')),
    source VARCHAR NOT NULL CHECK (source IN ('ai', 'ai_regenerated', 'doctor_edit')),
    chief_complaint TEXT NOT NULL DEFAULT '',
    discussion_points JSONB NOT NULL DEFAULT '[]'::jsonb,
    assessment TEXT NOT NULL DEFAULT '',
    plan TEXT NOT NULL DEFAULT '',
    created_by_auth_id VARCHAR REFERENCES authentication(auth_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_by_auth_id VARCHAR REFERENCES authentication(auth_id),
    approved_at TIMESTAMPTZ,
    CONSTRAINT uq_consultation_notes_version UNIQUE (consultation_id, version)
);

CREATE INDEX IF NOT EXISTS idx_consultation_notes_consultation_id ON consultation_notes(consultation_id);
