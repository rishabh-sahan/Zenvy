# Zenvy Conversation Service

## Overview

This is a FastAPI conversation service. **Redis** holds active runtime session state. **PostgreSQL** holds durable records. Both stores are joined by the same `session_id`.

## API contract

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/staff/login`
- `POST /api/v1/sessions`
- `GET /api/v1/sessions/{session_id}`
- `POST /api/v1/sessions/{session_id}/handoff`
- `POST /api/v1/sessions/{session_id}/turns`
- `GET /api/v1/sessions/{session_id}/turns`
- `POST /api/v1/appointments`
- `POST /api/v1/appointments/{appointment_id}/cancel`
- `GET /api/v1/appointments/session/{session_id}`
- `GET /api/v1/doctors?query=&hospital_id=&city=`
- `GET /api/v1/doctors/{doctor_id}`
- `GET /api/v1/doctors/{doctor_id}/slots?date=YYYY-MM-DD[&near=HH:MM&limit=3]`
- `POST /api/v1/slots/{slot_id}/hold`
- `POST /api/v1/slots/{slot_id}/release`

### Consultation recording and the AI scribe

- `POST /api/v1/appointments/{id}/consent` and `GET .../consent` - the patient (with their `auth_id`) or the treating doctor (staff token)
- `GET /api/v1/consent-message?language=en|hi|kn`
- `GET /api/v1/doctor/appointments` and `GET /api/v1/patients/{auth_id}/appointments`
- `POST /api/v1/consultations`, `GET /api/v1/consultations/{id}`
- `POST /api/v1/consultations/{id}/audio` (raw WAV, stored encrypted), `PUT .../transcript`, `PATCH .../status`, `PATCH .../turns/{turn_id}`
- `POST .../notes` (a new version), `POST .../notes/{note_id}/approve`, `DELETE .../recording`

Recording needs consent; only the treating doctor can open a consultation; notes
are versioned and an approved version is locked. Set `AUDIO_ENCRYPTION_KEY`
(`python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"`).
Delete expired recordings with `python -m app.db.purge_recordings`. See
`RETENTION_POLICY.md`. Migration `010_consultations.sql` adds the tables.

### Reminders, follow-ups, cancel and reschedule

- Every booking schedules messages in the `reminders` table (the single record of
  everything sent): to the patient a booking confirmation, a reminder 24 hours and
  2 hours before; to the doctor a "new appointment" notice and the same two
  reminders. Reminders whose time has already passed are skipped.
- A background scheduler (started with the service, one tick every
  `REMINDER_POLL_SECONDS`, default 30) sends what is due. Each reminder is claimed
  with one atomic UPDATE, so two schedulers can never send the same one. A failed
  send is retried once, `REMINDER_RETRY_MINUTES` later (default 5).
- `REMINDER_MODE=mock` (default) records the message and sends nothing;
  `live` sends through WhatsApp using the approved templates in
  `WHATSAPP_TEMPLATES.md`. `GET /healthz/scheduler` shows whether it is running.
- `POST /appointments/{id}/cancel` and `POST /appointments/{id}/reschedule`
  (`{"slot_id": ...}`; patient with `auth_id`/`session_id`, or the doctor's staff
  token). Reschedule keeps the same doctor and is atomic: the new slot is claimed,
  the new appointment created and the old one cancelled in one transaction, or
  nothing changes. Started appointments, and ones that have a consultation, cannot
  be changed (`409 already_started` / `has_consultation`). Both people are
  notified and the old reminders are stopped.
- `GET /appointments/{id}/history` (the treating doctor) lists what happened
  and every message with its status.
- Follow-ups: when a note's plan says "come back next week", "review in 2 weeks"
  and similar, a suggestion is stored (`GET/PUT/DELETE /consultations/{id}/follow-up`).
  Approving the note books it at the same time of day (or the nearest free slot)
  with the same locking as any booking; `POST .../follow-up/book` books it
  afterwards. Only time-based phrases are recognised, in English.
- Migration `011_followups_reminders.sql` adds the tables and columns.

### Agents and medication

Three agents work together; language models understand and draft, plain code does the work.

* **Patient agent** (gateway, `services/agents/patient_agent.py`): answers "what medicines do I take?", "when is my next dose?"
  and "I took my medicine" by chat or voice. It only repeats what the doctor signed.
* **Doctor agent** (gateway, `services/agents/doctor_agent.py`, `POST /doctor/api/assistant`): the Assistant panel on the
  doctor page. Schedule, what waits for approval, updates, a summary of this doctor's own earlier visits with the patient,
  a draft prescription, a draft follow-up date. It never approves or signs.
* **Coordinator** (Team C, `app/services/agent_service.py`): booking, cancel, reschedule, follow-up and signed-prescription
  events are queued in `agent_events` (each claimed once, retried, deduplicated) and turned into messages for the doctor
  and the patient (`agent_messages`). Everything the agents do is in `agent_actions`. It runs inside the existing scheduler.

Medication flow: the scribe drafts a medicine list from the transcript (a medicine whose name is not in the transcript or the
plan is thrown away) -> the doctor edits it in the **Prescription** card and signs -> the coordinator schedules a dose and a
reminder for every dose time (08:00 / 14:00 / 21:00 style, editable) -> the patient sees **Your medicines** and taps "I took
it" -> doses nobody marked within `MISSED_AFTER_HOURS` become "missed", and two missed doses in a day alert the doctor once.
A change is a new signed version; the old version's remaining doses are cancelled.

- `GET/PUT /consultations/{id}/prescription`, `POST .../prescription/sign`, `POST .../prescription/carry-forward` (the doctor)
- `GET /patients/{auth_id}/medications`, `POST /patients/{auth_id}/doses/taken`, `POST .../doses/{dose_id}/taken`, `POST .../messages/read`
- `GET /agent/messages`, `POST /agent/messages/read`, `GET /appointments/{id}/patient-history`, `POST /agents/actions`
- Migrations `012_agents.sql` and `013_prescriptions.sql`. Settings: `MISSED_AFTER_HOURS` (3), `MEDICATION_DEFAULT_DAYS` (7),
  `META_WHATSAPP_MEDICATION_TEMPLATE_NAME`.
- Limits: no allergy or drug-interaction checking (the doctor is responsible); no medicine advice from any agent.

### Doctors, slots and double-booking

Every bookable time is a row in `doctor_slots` (generated on demand from each
doctor's weekly schedule, 14 days ahead, IST). Booking is two steps and each
step is one atomic database statement, so two patients can never get the same
slot:

1. `POST /slots/{slot_id}/hold` with `{"session_id": ...}` reserves the slot
   for that conversation for `SLOT_HOLD_SECONDS` (default 300). Anyone else
   gets `409 slot_unavailable`; the slot is no longer listed as free.
2. `POST /appointments` with `{"session_id", "patient_uhid", "slot_id"}` turns
   the held slot into a booked one and creates the appointment in a single
   transaction. Doctor and time come from the slot. Not held by this session
   -> `409 slot_unavailable`.

`POST /slots/{slot_id}/release` gives a hold back, and
`POST /appointments/{id}/cancel` (staff token, or the booking `session_id`)
cancels an appointment and frees its slot. A hold that expires is treated as
free. A partial unique index on `ai_appointments(slot_id)` is a last safety net.

`POST /appointments` without `slot_id` still works (older callers); set
`REQUIRE_SLOT_FOR_BOOKING=true` to make the slot lock mandatory.

Setup after applying migrations 008-009:

```bash
python -m app.db.seed_doctors
# optional dummy staff logins for the seeded doctors (no default password):
SEED_DOCTOR_PASSWORD='choose-one' python -m app.db.seed_doctors
```

Tests: `python -m pytest` (SQLite). The real-Postgres concurrency tests need a
throwaway database, see `tests/test_slots_postgres.py` and
`../docker-compose.local-db.yml`.

Audit records are written automatically for user registration and appointment
creation. Audit endpoints require a staff bearer token. Existing users can be
made staff by setting `authentication.role` to `staff` through a controlled
database migration or administrative process; public registration always
creates `patient` users.

Configure `META_WHATSAPP_ACCESS_TOKEN`, `META_WHATSAPP_PHONE_NUMBER_ID`,
`META_WHATSAPP_API_VERSION`, and `META_WHATSAPP_TEMPLATE_LANGUAGE` in `.env`.
Set the approved template names with `META_WHATSAPP_APPOINTMENT_TEMPLATE_NAME`
and `META_WHATSAPP_WELCOME_TEMPLATE_NAME`. The sender uses Meta's Graph API and
passes named body variables (`name`, `doctor`, `date`, `time`, `location`, and
`booking_id` for appointments). Keep access tokens out of source control and
rotate any credential that has been shared publicly. The local `.env` file is
ignored by Git; configure deployment secrets in the hosting environment.

## Run locally

1. Create a PostgreSQL database and update `.env`.
2. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

3. Apply the database migrations:

```bash
..\.venv\Scripts\python.exe -m app.db.init_db
```

4. Configure `REDIS_URL` to a reachable Redis instance. The default is local Redis at `redis://localhost:6379/0`.

5. Start the app:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Notes

- Uses SQLAlchemy ORM and Pydantic validation.
- Database URL is loaded from `.env` and must use `sslmode=require` for Supabase.
- Active session state is stored in Redis (`REDIS_URL`) under `zenvy:session:{session_id}` with a sliding TTL (`SESSION_TTL_SECONDS`, default 3600).
- PostgreSQL is the durable store. `POST /api/v1/sessions/{session_id}/handoff` flushes Redis into Postgres, marks the session completed, and deletes the runtime key.
- Schema is defined and versioned in `db/migrations/`. Run `app/db/init_db.py` before starting the service; the server does not modify the database at startup.
- `/healthz` returns HTTP 200 only when Redis responds to `PING`; Redis outages return HTTP 503 with `{"status":"degraded","redis":false}`.

## Manual Redis test

Supabase provides PostgreSQL, not Redis. Use a separate hosted Redis service and put its complete connection URL in `.env`:

```text
REDIS_URL=rediss://:<PASSWORD>@<HOST>:<PORT>/0
```

Use `rediss://` when the provider requires TLS. Do not commit a URL containing credentials.

Verify the configured Redis connection directly:

```powershell
..\.venv\Scripts\python.exe -c "from app.db.redis import get_redis; print(get_redis().ping())"
```

Expected output is:

```text
True
```

With the API running, verify the application connection:

```powershell
Invoke-WebRequest -UseBasicParsing http://localhost:8000/healthz
```

Expected response: HTTP `200` and `{"status":"ok","redis":true}`. If the hosted Redis service is unreachable, the response is HTTP `503` and `{"status":"degraded","redis":false}`.

After changing `REDIS_URL`, restart Uvicorn and repeat both checks.

## PostgreSQL setup (local)

1. Create a Postgres user and database (example):

```bash
# run as the postgres superuser or via sudo
psql -U postgres -c "CREATE USER zenvy_user WITH PASSWORD 'zenvy_pass';"
psql -U postgres -c "CREATE DATABASE zenvy_db OWNER zenvy_user;"
```

Alternatively run the provided SQL helper:

```bash
# from the repo root
psql -U postgres -f db/create_postgres.sql
```

2. Update `.env` with your DB connection string (example):

```
DATABASE_URL=postgresql+psycopg://zenvy_user:zenvy_pass@localhost:5432/zenvy_db
REDIS_URL=redis://localhost:6379/0
SESSION_TTL_SECONDS=3600
```

3. Apply the migrations:

```bash
..\.venv\Scripts\python.exe -m app.db.init_db
```

This applies the initial schema and repairs databases previously created with SQLAlchemy `create_all`, including JSONB types and foreign-key delete actions.

4. Start the server:

```bash
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

5. Example curl requests to verify the flow:

```bash
# create a session
curl -X POST http://localhost:8000/api/v1/sessions -H 'Content-Type: application/json' \
	-d '{"user_id":"user_001","channel":"phone","language":"en"}'

# add a turn (replace SESSION_ID)
curl -X POST http://localhost:8000/api/v1/sessions/SESSION_ID/turns -H 'Content-Type: application/json' \
	-d '{"speaker":"user","content":"I need an appointment","language":"en"}'

# get session (Redis while active, Postgres after handoff)
curl http://localhost:8000/api/v1/sessions/SESSION_ID

# handoff runtime state to Postgres
curl -X POST http://localhost:8000/api/v1/sessions/SESSION_ID/handoff
```

6. Verify rows directly in Postgres:

```bash
psql -U zenvy_user -d zenvy_db -c "SELECT * FROM sessions;"
psql -U zenvy_user -d zenvy_db -c "SELECT * FROM conversation_turns;"
```
