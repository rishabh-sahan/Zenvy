# Deploying Zenvy on Render's FREE plan (no card)

`render.yaml` (the Blueprint) uses paid plans, which is why Render asks for a card. The free plan
cannot run that layout: it has no private services, no disks and no Shell. This guide runs
**everything in ONE free web service** instead (`Dockerfile.render`): gateway, speech-to-text,
text-to-speech, the data service and Redis, in one container. Only the gateway is public.

Your data lives in **Supabase** (already migrated), so nothing important is lost when Render restarts.

## What the free plan means (be aware)
| Limit | Effect |
|---|---|
| Sleeps after 15 minutes without a request | The first request afterwards takes about 30-60 seconds. The reminder scheduler is paused while asleep. A free pinger (below) keeps it awake. |
| 512 MB RAM | Enough for the pilot (about 250 MB used). A very long recording upload could run out of memory. |
| No persistent disk | Encrypted recordings are lost when the service restarts or redeploys. **Transcripts, notes, prescriptions and everything else stay safe in Supabase.** |
| 750 free hours a month | One service can stay on all month. |
| No Shell | The doctors are seeded automatically at start-up (see `SEED_DOCTOR_PASSWORD`). |

This is good for demos and testing. It is not for real patients (login has no OTP yet, and recordings
are not kept).

## 1. Prepare
1. Code on GitHub with `Dockerfile.render` and `render/start.sh` (branch `team-a/rishabh` or `main`).
2. From Supabase > Project settings > Database copy the **Session pooler** connection string and write it as
   `postgresql+psycopg://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require`
   (the direct `db.<ref>.supabase.co` host is IPv6-only; Render cannot reach it).
3. Create two secrets on your own computer (keep them safe; losing the encryption key makes stored recordings unreadable):
   ```
   python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"   # AUDIO_ENCRYPTION_KEY
   python -c "import secrets;print(secrets.token_urlsafe(48))"                     # AUTH_TOKEN_SECRET
   ```

## 2. Create the service (Render dashboard)
1. **Projects** > open (or create) a project > **New** > **Web Service**.
2. Connect the GitHub repo `rishabh-sahan/Zenvy`; choose the branch.
3. Settings:
   | Field | Value |
   |---|---|
   | Name | `zenvy` |
   | Language / Runtime | **Docker** |
   | Dockerfile Path | `./Dockerfile.render` |
   | Docker Build Context | `.` |
   | Region | **Singapore** |
   | Instance Type | **Free** |
   | Health Check Path | `/health` |
4. **Environment Variables** (Add):
   | Key | Value |
   |---|---|
   | `DATABASE_URL` | the pooler string from step 1.2 |
   | `SARVAM_API_KEY` | your Sarvam key |
   | `AUTH_TOKEN_SECRET` | from step 1.3 |
   | `AUDIO_ENCRYPTION_KEY` | from step 1.3 |
   | `SEED_DOCTOR_PASSWORD` | a strong password for the doctor logins (seeds on start; safe to repeat) |
   | `REMINDER_MODE` | `mock` (change to `live` after the smoke test) |
   | `META_WHATSAPP_ACCESS_TOKEN` | your Meta token |
   | `META_WHATSAPP_PHONE_NUMBER_ID` | your phone number id |
   | `META_WHATSAPP_BUSINESS_ACCOUNT_ID` | your business account id |
   | `META_WHATSAPP_API_VERSION` | `v23.0` |
   | `META_WHATSAPP_TEMPLATE_LANGUAGE` | `en` |
5. **Create Web Service**. The first build takes about 5-10 minutes. Watch the **Logs**; you should see
   `[start] gateway on port 10000`.

## 3. Smoke test
Open `https://<your-service>.onrender.com`.
1. `/health` returns ok.
2. Patient page: log in, book a doctor, see "Your appointments".
3. `/doctor`: sign in as `+910000000001` with `SEED_DOCTOR_PASSWORD`.
4. Upload a short recording; sign a prescription; "Your medicines" shows it.

Then set `REMINDER_MODE=live` to start real WhatsApp messages (test with your own number first).

## Troubleshooting

| What you see | Cause | Fix |
|---|---|---|
| Patient page says **"Can't reach the server right now"**, or every API call returns 502 "data service is not reachable" | Only the gateway is running: the service was built from the default `./Dockerfile`, not `./Dockerfile.render`, or a **Docker Command** overrides the start script | Service > **Settings** > Build & Deploy: set **Dockerfile Path** to `./Dockerfile.render`, make **Docker Command** empty, save, then **Manual Deploy > Clear build cache & deploy** |
| Logs end with `[start] missing required environment variable: X` | That variable is not set | Add `X` under Environment and redeploy |
| Logs end with `[start] migrations failed` | `DATABASE_URL` is wrong or unreachable | Use the Supabase **Session pooler** string (not `db.<ref>.supabase.co`) and check the password |
| The traceback ends with `connection to server at "2406:...", port 5432 failed: Network is unreachable` | `DATABASE_URL` uses Supabase's direct host (`db.<ref>.supabase.co`), which resolves to **IPv6 only**; Render cannot reach it | Use the **Session pooler** (IPv4): `postgresql+psycopg://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require`. Find `<region>` in Supabase > Connect > Session pooler (this project: `ap-southeast-1`). A `@` in the password must be written `%40`. |
| Doctor login on the live site says "Invalid staff credentials" | The doctors on the live site were created with **`SEED_DOCTOR_PASSWORD`**, not the local test password | Sign in with the `SEED_DOCTOR_PASSWORD` value you set in Render (changing the variable later does not change existing logins) |
| A table looks empty in the Supabase dashboard | Wrong project, wrong schema, or a filter | Open project `<ref>`, schema **public**, clear filters, sort by `created_at` descending; or run `select count(*) from conversation_turns;` in the SQL Editor |
| Logs show `[start] ... did not come up` | An internal service crashed at start-up | Read the Python error just above that line |

A healthy start prints, in order: `[start] redis`, `applying database migrations`, `data service (team-c)`,
`speech-to-text ... text-to-speech`, `waiting for the internal services`, `gateway on port 10000`.
If you do not see these lines, the start script is not running.

## 4. Keep it awake (free)
Create a free account at uptimerobot.com and add an HTTP monitor for `https://<your-service>.onrender.com/health`
every 5 minutes. That keeps the instance (and the reminder scheduler) running.

## 5. Moving to production later
Use `render.yaml` (a Blueprint) and a paid plan: separate private services, a 5 GB recordings disk,
and a Shell. See `DEPLOY_RENDER.md`.
