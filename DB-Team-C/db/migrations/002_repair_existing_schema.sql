-- Migration 002: align databases previously created with SQLAlchemy create_all.

ALTER TABLE sessions
    ALTER COLUMN session_metadata TYPE JSONB USING session_metadata::jsonb;

ALTER TABLE conversation_turns
    ALTER COLUMN content TYPE TEXT USING content::text,
    ALTER COLUMN input_text TYPE TEXT USING input_text::text,
    ALTER COLUMN response_text TYPE TEXT USING response_text::text,
    ALTER COLUMN turn_metadata TYPE JSONB USING turn_metadata::jsonb;

ALTER TABLE ai_appointments
    ALTER COLUMN booking_info TYPE JSONB USING booking_info::jsonb,
    ALTER COLUMN appointment_metadata TYPE JSONB USING appointment_metadata::jsonb;

ALTER TABLE escalations
    ALTER COLUMN reason TYPE TEXT USING reason::text,
    ALTER COLUMN escalation_metadata TYPE JSONB USING escalation_metadata::jsonb;

ALTER TABLE audit_log
    ALTER COLUMN relevant_metadata TYPE JSONB USING relevant_metadata::jsonb,
    ALTER COLUMN before_value TYPE JSONB USING before_value::jsonb,
    ALTER COLUMN after_value TYPE JSONB USING after_value::jsonb;

ALTER TABLE conversation_turns DROP CONSTRAINT IF EXISTS conversation_turns_session_id_fkey;
ALTER TABLE ai_appointments DROP CONSTRAINT IF EXISTS ai_appointments_session_id_fkey;
ALTER TABLE escalations DROP CONSTRAINT IF EXISTS escalations_session_id_fkey;
ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS audit_log_session_id_fkey;

ALTER TABLE conversation_turns
    ADD CONSTRAINT conversation_turns_session_id_fkey
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE;
ALTER TABLE ai_appointments
    ADD CONSTRAINT ai_appointments_session_id_fkey
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE;
ALTER TABLE escalations
    ADD CONSTRAINT escalations_session_id_fkey
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE;
ALTER TABLE audit_log
    ADD CONSTRAINT audit_log_session_id_fkey
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL;