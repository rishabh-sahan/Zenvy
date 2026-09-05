-- Migration 003: patient authentication table.
--
-- This table already exists in the hosted Supabase database but was created
-- outside the migration files, so a fresh database built only from db/migrations/
-- was missing it. Declared here with IF NOT EXISTS so it is a no-op against the
-- existing database and reproducible on a new one.
--
-- password_hash is NOT NULL. Externally created accounts hold a Werkzeug
-- scrypt hash; the phone-only web login self-registers new numbers and stores
-- the UNUSABLE_PASSWORD_HASH sentinel from app/models/authentication.py, which
-- cannot verify against any password. The column stays so password
-- verification can be turned on without a schema change.

CREATE TABLE IF NOT EXISTS authentication (
    auth_id VARCHAR PRIMARY KEY,
    phone_no VARCHAR NOT NULL UNIQUE,
    password_hash VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_authentication_phone_no ON authentication(phone_no);
