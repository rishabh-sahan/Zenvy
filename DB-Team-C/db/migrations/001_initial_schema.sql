-- Migration 001: durable PostgreSQL schema for Zenvy Conversation Service.
-- Runtime session state lives in Redis and is joined to these tables by session_id.

DO $$ BEGIN
    CREATE TYPE session_status AS ENUM ('active', 'completed');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE appointment_status AS ENUM ('pending', 'confirmed', 'completed', 'cancelled');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE escalation_status AS ENUM ('open', 'in_progress', 'resolved', 'closed');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS sessions (
    session_id VARCHAR PRIMARY KEY,
    user_id VARCHAR NOT NULL,
    uhid VARCHAR,
    channel VARCHAR NOT NULL,
    language VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    status session_status NOT NULL DEFAULT 'active',
    session_metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_uhid ON sessions(uhid);

CREATE TABLE IF NOT EXISTS conversation_turns (
    turn_id VARCHAR PRIMARY KEY,
    session_id VARCHAR NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    speaker VARCHAR NOT NULL,
    content TEXT NOT NULL,
    input_text TEXT,
    response_text TEXT,
    language VARCHAR NOT NULL,
    sequence_number INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    turn_metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_conversation_turns_session_id ON conversation_turns(session_id);

CREATE TABLE IF NOT EXISTS ai_appointments (
    appointment_id VARCHAR PRIMARY KEY,
    session_id VARCHAR NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    patient_uhid VARCHAR NOT NULL,
    doctor_name VARCHAR NOT NULL,
    appointment_datetime TIMESTAMPTZ NOT NULL,
    status appointment_status NOT NULL DEFAULT 'pending',
    booking_info JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    appointment_metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_ai_appointments_session_id ON ai_appointments(session_id);
CREATE INDEX IF NOT EXISTS idx_ai_appointments_patient_uhid ON ai_appointments(patient_uhid);

CREATE TABLE IF NOT EXISTS escalations (
    escalation_id VARCHAR PRIMARY KEY,
    session_id VARCHAR NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    emergency_type VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    status escalation_status NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    handled_by VARCHAR,
    escalation_metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_escalations_session_id ON escalations(session_id);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id VARCHAR PRIMARY KEY,
    session_id VARCHAR REFERENCES sessions(session_id) ON DELETE SET NULL,
    user_id VARCHAR,
    action VARCHAR NOT NULL,
    actor VARCHAR NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    relevant_metadata JSONB,
    before_value JSONB,
    after_value JSONB
);

CREATE INDEX IF NOT EXISTS idx_audit_log_session_id ON audit_log(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_user_id ON audit_log(user_id);
