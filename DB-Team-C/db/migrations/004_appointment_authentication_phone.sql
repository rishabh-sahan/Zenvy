-- Migration 004: link appointments to the registered WhatsApp phone number.

ALTER TABLE ai_appointments
    ADD COLUMN IF NOT EXISTS phone_no VARCHAR;

CREATE INDEX IF NOT EXISTS idx_ai_appointments_phone_no ON ai_appointments(phone_no);

DO $$ BEGIN
    ALTER TABLE ai_appointments
        ADD CONSTRAINT ai_appointments_phone_no_fkey
        FOREIGN KEY (phone_no) REFERENCES authentication(phone_no);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;