# Zenvy Conversation Service

## Overview

This is a FastAPI conversation service. **Redis** holds active runtime session state. **PostgreSQL** holds durable records. Both stores are joined by the same `session_id`.

## API contract

- `POST /api/v1/sessions`
- `GET /api/v1/sessions/{session_id}`
- `POST /api/v1/sessions/{session_id}/handoff`
- `POST /api/v1/sessions/{session_id}/turns`
- `GET /api/v1/sessions/{session_id}/turns`
- `POST /api/v1/appointments`
- `GET /api/v1/appointments/session/{session_id}`

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
