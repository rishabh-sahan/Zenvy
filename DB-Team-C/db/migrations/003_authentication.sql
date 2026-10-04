-- Migration 003: phone and password authentication records.

CREATE TABLE IF NOT EXISTS authentication (
    auth_id VARCHAR PRIMARY KEY,
    phone_no VARCHAR NOT NULL UNIQUE,
    password_hash VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_authentication_phone_no ON authentication(phone_no);