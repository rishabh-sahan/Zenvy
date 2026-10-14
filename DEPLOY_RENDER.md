# Deploying Zenvy on Render

Architecture on Render (all in the **Singapore** region, the closest to India):

| Service | Type | Reachable from | Notes |
|---|---|---|---|
| `zenvy-gateway` | Web service | the internet (HTTPS) | patient page `/`, doctor page `/doctor`, API |
| `zenvy-stt` | Private service | other services only | speech to text (calls Sarvam) |
| `zenvy-tts` | Private service | other services only | text to speech (calls Sarvam) |
| `zenvy-team-c` | Private service + 5 GB disk | other services only | data service, reminder scheduler, encrypted recordings |
| `zenvy-redis` | Key Value | other services only | chat state |
| Supabase | outside Render | team-c only | PostgreSQL |

The microphone works because Render serves the gateway over HTTPS.

## 1. Before you start
1. The code must be on GitHub, with `render.yaml` at the repo root (branch `team-a/rishabh` or `main`).
2. In **Supabase** > Project settings > Database, copy the **Session pooler** connection string
   (host like `aws-0-ap-south-1.pooler.supabase.com`, port 5432, user `postgres.<project-ref>`).
   Turn it into the app's format and keep it ready:
   `postgresql+psycopg://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require`
   Use the pooler, not `db.<ref>.supabase.co`: the direct host is IPv6-only and Render cannot reach it.
3. Have ready: `SARVAM_API_KEY`, and the three Meta values `META_WHATSAPP_ACCESS_TOKEN`,
   `META_WHATSAPP_PHONE_NUMBER_ID`, `META_WHATSAPP_BUSINESS_ACCOUNT_ID`.
4. **The first deploy changes your Supabase database.** It applies the new migrations 008-013
   (slots, consultations, follow-ups, reminders, agents, prescriptions) to the database in
   `DATABASE_URL`. Take a backup first (Supabase > Database > Backups), or point `DATABASE_URL`
   at a fresh empty Supabase project for the pilot.

## 2. Create everything from the Blueprint
1. Render dashboard > **New** > **Blueprint**.
2. Connect the GitHub repo `rishabh-sahan/Zenvy` and pick the branch that has `render.yaml`.
3. Render lists the 5 services. Fill in the values it asks for:
   - `SARVAM_API_KEY` (asked for gateway, stt and tts: paste the same key three times)
   - `DATABASE_URL` (the pooler string from step 1.2)
   - the three `META_WHATSAPP_*` values
4. Click **Apply**. The first build takes about 5-10 minutes.
   `AUTH_TOKEN_SECRET` and `AUDIO_ENCRYPTION_KEY` are generated for you.
5. **Copy `AUDIO_ENCRYPTION_KEY`** from `zenvy-team-c` > Environment into a safe place.
   If it is lost, stored recordings can never be decrypted.

The database migrations (008-013 and the earlier ones) are applied automatically before each
`zenvy-team-c` deploy by `python -m app.db.init_db`.

## 3. Seed the doctors (once)
`zenvy-team-c` > **Shell**, then:

```
SEED_DOCTOR_PASSWORD='choose-a-strong-password' python -m app.db.seed_doctors
```

This creates 10 hospitals, 50 doctors and their logins (`+910000000001` ...). Use a strong password,
not the local test one.

## 4. Smoke test
Open `https://zenvy-gateway.onrender.com` (your URL is shown on the gateway service).
1. `/health` shows ok.
2. Patient page: log in with a phone number, book a doctor, check "Your appointments".
3. `/doctor`: sign in as `+910000000001` with the seed password; the appointment is listed.
4. Record or upload a short consultation; the transcript and a draft note appear.
5. Sign a prescription; "Your medicines" appears on the patient page.

## 5. Turn on real WhatsApp messages
In `zenvy-team-c` > Environment set `REMINDER_MODE` to `live` and save (it redeploys).
Messages then go to real phone numbers, so test with your own number first.

## 6. Running costs (approximate, check Render's pricing page)
Gateway on Standard, three Starter services, 5 GB disk, free Key Value: roughly $50-60 a month.
The gateway can be moved to Starter if it never runs out of memory on long recordings.

## 7. Things to know
- `zenvy-team-c` must stay at **one instance** (it holds the recordings disk and the scheduler).
  A deploy of that service has a short pause; reminders catch up afterwards.
- Do not use the free plan for `zenvy-team-c`: free services sleep and cannot have a disk.
- Patient login is phone-number only (no OTP yet). Do not open this to real patients until OTP exists.
- The seeded doctor phone numbers are placeholders, so WhatsApp doctor notices are skipped until
  real numbers are set.
- Auto deploys: pushing to the connected branch redeploys. Turn this off per service if you prefer.
