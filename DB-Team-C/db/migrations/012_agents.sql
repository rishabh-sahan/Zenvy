-- Migration 012: the three-agent system.
--
--   agent_events    work for the coordinator: something happened (a booking, a cancel, a signed
--                   prescription, ...). Each row is claimed with one conditional UPDATE, so it is
--                   handled exactly once even if several schedulers run.
--   agent_messages  what the coordinator tells the doctor agent / the patient agent (shown in the
--                   doctor's Assistant panel and the patient's "Your medicines" card).
--   agent_actions   an audit trail of what each agent did. No phone numbers are stored.

CREATE TABLE IF NOT EXISTS agent_events (
    event_id VARCHAR PRIMARY KEY,
    kind VARCHAR NOT NULL,
    appointment_id VARCHAR REFERENCES ai_appointments(appointment_id),
    payload JSONB,
    status VARCHAR NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    dedupe_key VARCHAR UNIQUE,
    last_error VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_events_pending ON agent_events(status, created_at);

CREATE TABLE IF NOT EXISTS agent_messages (
    message_id VARCHAR PRIMARY KEY,
    recipient_type VARCHAR NOT NULL CHECK (recipient_type IN ('doctor', 'patient')),
    recipient_auth_id VARCHAR NOT NULL REFERENCES authentication(auth_id),
    appointment_id VARCHAR REFERENCES ai_appointments(appointment_id),
    kind VARCHAR NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_recipient ON agent_messages(recipient_auth_id, read_at);

CREATE TABLE IF NOT EXISTS agent_actions (
    action_id VARCHAR PRIMARY KEY,
    agent VARCHAR NOT NULL CHECK (agent IN ('patient', 'doctor', 'coordinator')),
    actor_auth_id VARCHAR REFERENCES authentication(auth_id),
    tool VARCHAR NOT NULL,
    summary VARCHAR,
    result VARCHAR NOT NULL DEFAULT 'ok' CHECK (result IN ('ok', 'refused', 'error')),
    appointment_id VARCHAR REFERENCES ai_appointments(appointment_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_actions_agent ON agent_actions(agent, created_at);
