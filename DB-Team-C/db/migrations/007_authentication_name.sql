-- Migration 007: store the patient's name for personalized notifications.

ALTER TABLE authentication
    ADD COLUMN IF NOT EXISTS name VARCHAR NOT NULL DEFAULT 'Zenvy user';