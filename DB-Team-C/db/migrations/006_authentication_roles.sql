-- Migration 006: support staff-only authentication.

ALTER TABLE authentication
    ADD COLUMN IF NOT EXISTS role VARCHAR NOT NULL DEFAULT 'patient';

CREATE INDEX IF NOT EXISTS idx_authentication_role ON authentication(role);