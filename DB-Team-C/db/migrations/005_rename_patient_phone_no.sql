-- Migration 005: use an explicit patient phone column name on appointments.

ALTER TABLE ai_appointments
    DROP CONSTRAINT IF EXISTS ai_appointments_phone_no_fkey;

ALTER TABLE ai_appointments
    RENAME COLUMN phone_no TO patient_phone_no;

ALTER TABLE ai_appointments
    ADD CONSTRAINT ai_appointments_patient_phone_no_fkey
    FOREIGN KEY (patient_phone_no) REFERENCES authentication(phone_no);

ALTER INDEX IF EXISTS idx_ai_appointments_phone_no
    RENAME TO idx_ai_appointments_patient_phone_no;