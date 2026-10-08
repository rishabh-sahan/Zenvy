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
