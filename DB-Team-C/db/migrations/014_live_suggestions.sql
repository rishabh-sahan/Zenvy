-- Migration 014: Live Assist - the AI's suggestions to the doctor during a consultation.
--
-- Kept in their own table, never in the medical note. They follow the transcript's retention:
-- deleted with the recording/transcript (consent withdrawal, "Delete recording", the daily purge).
-- Holds no phone numbers.

CREATE TABLE IF NOT EXISTS live_suggestions (
    suggestion_id VARCHAR PRIMARY KEY,
    consultation_id VARCHAR NOT NULL REFERENCES consultations(consultation_id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    kind VARCHAR NOT NULL CHECK (kind IN ('ask', 'red_flag', 'history', 'wrap_up', 'consider')),
    text TEXT NOT NULL,
    evidence TEXT,
    source VARCHAR NOT NULL DEFAULT 'model' CHECK (source IN ('model', 'rule')),
    status VARCHAR NOT NULL DEFAULT 'shown'
        CHECK (status IN ('shown', 'dismissed', 'pinned', 'added', 'asked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acted_at TIMESTAMPTZ,
    CONSTRAINT uq_live_suggestions_seq UNIQUE (consultation_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_live_suggestions_consultation ON live_suggestions(consultation_id, seq);
