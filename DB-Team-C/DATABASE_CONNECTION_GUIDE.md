# Zenvy Backend Database Connection Guide

## Database Overview

The Zenvy Conversation Service uses a PostgreSQL database hosted on **Supabase** to store conversation sessions, turns, appointments, escalations, and audit logs.

---

## Connection Details

### Host
```
db.naigzwdhcpfdofjeiobb.supabase.co
```

### Port
```
5432
```

### Database Name
```
postgres
```

### Username
```
postgres
```

### Password
**⚠️ Secure Secret** — Request from project lead or environment manager

### Connection String (SQLAlchemy/Python)
```
postgresql+psycopg://postgres:YOUR_PASSWORD@db.naigzwdhcpfdofjeiobb.supabase.co:5432/postgres?sslmode=require
```

### Connection String (psql/CLI)
```bash
psql -h db.naigzwdhcpfdofjeiobb.supabase.co -U postgres -d postgres -p 5432 --set=sslmode=require
```

### Connection String (Node.js/JavaScript)
```javascript
const { Pool } = require('pg');

const pool = new Pool({
  host: 'db.naigzwdhcpfdofjeiobb.supabase.co',
  port: 5432,
  database: 'postgres',
  user: 'postgres',
  password: 'YOUR_PASSWORD',
  ssl: {
    rejectUnauthorized: false,
  },
});
```

---

## Database Schema

### 1. **sessions** table
Stores conversation session metadata.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| session_id | VARCHAR | NO | Primary Key - Unique session identifier (UUID) |
| user_id | VARCHAR | NO | Patient/User identifier |
| uhid | VARCHAR | YES | Unique Health ID (primary patient identifier) |
| channel | VARCHAR | NO | Communication channel (`phone`, `sms`, `web`) |
| language | VARCHAR | NO | Language code (e.g., `en`, `hi`) |
| started_at | TIMESTAMPTZ | NO | Session start timestamp (default: NOW()) |
| ended_at | TIMESTAMPTZ | YES | Session end timestamp |
| status | ENUM | NO | Session status (`active`, `completed`) |
| session_metadata | JSONB | YES | Additional session context data |

**Indexes:**
- `session_id` (Primary Key)
- `user_id`
- `uhid`

---

### 2. **conversation_turns** table
Stores individual conversation messages/turns within a session.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| turn_id | VARCHAR | NO | Primary Key - Unique turn identifier (UUID) |
| session_id | VARCHAR | NO | Foreign Key → sessions.session_id |
| speaker | VARCHAR | NO | Who sent the message (`user`, `assistant`, `system`) |
| content | TEXT | NO | Full message content/transcript |
| input_text | TEXT | YES | Original user input |
| response_text | TEXT | YES | Assistant response |
| language | VARCHAR | NO | Language of this turn |
| sequence_number | INTEGER | NO | Order within the session (default: 1) |
| created_at | TIMESTAMPTZ | NO | Timestamp (default: NOW()) |
| turn_metadata | JSONB | YES | Additional turn context |

**Indexes:**
- `turn_id` (Primary Key)
- `session_id` (Foreign Key)

**Constraints:**
- Foreign Key: `session_id` → `sessions.session_id` (ON DELETE CASCADE)

---

### 3. **ai_appointments** table
Stores AI-generated appointment bookings.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| appointment_id | VARCHAR | NO | Primary Key - Unique appointment ID (UUID) |
| session_id | VARCHAR | NO | Foreign Key → sessions.session_id |
| patient_uhid | VARCHAR | NO | Patient's Unique Health ID |
| doctor_name | VARCHAR | NO | Assigned doctor name |
| appointment_datetime | TIMESTAMPTZ | NO | Scheduled appointment date/time |
| status | ENUM | NO | Appointment status (`pending`, `confirmed`, `completed`, `cancelled`) |
| booking_info | JSONB | YES | Booking details (clinic, slot, etc.) |
| created_at | TIMESTAMPTZ | NO | When appointment was created |
| appointment_metadata | JSONB | YES | Additional metadata |

**Indexes:**
- `appointment_id` (Primary Key)
- `session_id` (Foreign Key)
- `patient_uhid`

**Constraints:**
- Foreign Key: `session_id` → `sessions.session_id` (ON DELETE CASCADE)

---

### 4. **escalations** table
Stores escalation records when urgent/emergency intervention is needed.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| escalation_id | VARCHAR | NO | Primary Key - Unique escalation ID (UUID) |
| session_id | VARCHAR | NO | Foreign Key → sessions.session_id |
| reason | TEXT | NO | Why the escalation was triggered |
| emergency_type | VARCHAR | NO | Type of emergency (e.g., `medical`, `critical`, `behavioral`) |
| severity | VARCHAR | NO | Severity level (e.g., `low`, `medium`, `high`, `critical`) |
| status | ENUM | NO | Escalation status (`open`, `in_progress`, `resolved`, `closed`) |
| created_at | TIMESTAMPTZ | NO | When escalation was created |
| resolved_at | TIMESTAMPTZ | YES | When it was resolved |
| handled_by | VARCHAR | YES | Staff/agent who handled it |
| escalation_metadata | JSONB | YES | Additional escalation context |

**Indexes:**
- `escalation_id` (Primary Key)
- `session_id` (Foreign Key)

**Constraints:**
- Foreign Key: `session_id` → `sessions.session_id` (ON DELETE CASCADE)

---

### 5. **audit_log** table
Stores audit trail of all important actions for compliance and debugging.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| audit_id | VARCHAR | NO | Primary Key - Unique audit log ID (UUID) |
| session_id | VARCHAR | YES | Related session (Foreign Key) |
| user_id | VARCHAR | YES | User who performed the action |
| action | VARCHAR | NO | Action performed (e.g., `create_session`, `update_appointment`) |
| actor | VARCHAR | NO | System/service that performed the action |
| timestamp | TIMESTAMPTZ | NO | When the action occurred |
| relevant_metadata | JSONB | YES | Context about the action |
| before_value | JSONB | YES | State before the action |
| after_value | JSONB | YES | State after the action |

**Indexes:**
- `audit_id` (Primary Key)
- `session_id` (Foreign Key)
- `user_id`

**Constraints:**
- Foreign Key: `session_id` → `sessions.session_id` (ON DELETE SET NULL)

---

## API Endpoints

### Base URL
```
http://localhost:8000  (Development)
```

### Sessions
- `POST /api/v1/sessions` — Create a new session
- `POST /api/v1/sessions/{session_id}/turns` — Add a conversation turn
- `GET /api/v1/sessions/{session_id}/turns` — Get all turns in a session

### Appointments
- `POST /api/v1/appointments` — Create appointment
- `GET /api/v1/appointments/session/{session_id}` — Get appointments for a session

### Escalations
- `POST /api/v1/escalations` — Create escalation
- `GET /api/v1/escalations/session/{session_id}` — Get escalations for a session

### Audit Logs
- `POST /api/v1/audit` — Create audit log entry
- `GET /api/v1/audit/session/{session_id}` — Get audit logs for a session

### Health Check
- `GET /healthz` — Service health status

---

## Environment Variables

Add these to your `.env` file:

```bash
DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@db.naigzwdhcpfdofjeiobb.supabase.co:5432/postgres?sslmode=require
DEBUG=false
```

---

## Setup Instructions for Backend Developer

### 1. Clone the Repository
```bash
git clone <repository-url>
cd team-c
```

### 2. Create Virtual Environment
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment
Create `.env` file with the database connection details (request password from project lead):
```bash
DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@db.naigzwdhcpfdofjeiobb.supabase.co:5432/postgres?sslmode=require
```

### 5. Run Tests
```bash
pytest -v
```

### 6. Start the Server
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 7. Access API Documentation
Open browser: `http://localhost:8000/docs`

---

## Important Notes

- **SSL Required**: All Supabase connections must use `sslmode=require`
- **Password Handling**: Never commit the password to version control. Use environment variables.
- **Cascading Deletes**: Deleting a session will cascade-delete all related turns, appointments, and escalations.
- **UHID**: The `uhid` field in sessions is the primary patient identifier for healthcare integration.
- **Audit Trail**: All actions are logged in `audit_log` for compliance and debugging.
- **Timezone**: All timestamps are in `TIMESTAMPTZ` (timezone-aware).

---

