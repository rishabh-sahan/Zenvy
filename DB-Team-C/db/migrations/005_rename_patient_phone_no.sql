-- Migration 005: use an explicit patient phone column name on appointments.
--
-- Every step is guarded. The bare RENAME COLUMN this previously used aborted
-- the whole migration run on any database that already had patient_phone_no
-- (for example one built with SQLAlchemy create_all before 004 landed), since
-- 004 would then add a second phone_no column that could not be renamed over
-- it. This file is already recorded as applied on the hosted database, so the
-- guards only affect databases built from scratch.

ALTER TABLE ai_appointments
    DROP CONSTRAINT IF EXISTS ai_appointments_phone_no_fkey;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'ai_appointments' AND column_name = 'phone_no'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'ai_appointments' AND column_name = 'patient_phone_no'
    ) THEN
        ALTER TABLE ai_appointments RENAME COLUMN phone_no TO patient_phone_no;
    END IF;
END $$;

-- Covers a database that never had 004's phone_no column at all.
ALTER TABLE ai_appointments
    ADD COLUMN IF NOT EXISTS patient_phone_no VARCHAR;

DO $$ BEGIN
    ALTER TABLE ai_appointments
        ADD CONSTRAINT ai_appointments_patient_phone_no_fkey
        FOREIGN KEY (patient_phone_no) REFERENCES authentication(phone_no);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

ALTER INDEX IF EXISTS idx_ai_appointments_phone_no
    RENAME TO idx_ai_appointments_patient_phone_no;

CREATE INDEX IF NOT EXISTS idx_ai_appointments_patient_phone_no
    ON ai_appointments(patient_phone_no);
