# Consultation recordings: retention and protection

This describes what the code in this repository actually does for consultation
recordings, transcripts and notes (the Ambient Scribe). It is a working policy
for the pilot. Items marked **Gap** are not done yet and need a decision before
real patients are recorded.

## What is stored, and for how long

| Data | Where | Kept for | Removed by |
|---|---|---|---|
| Consent decision (who, when, wording version) | `consultation_consents` | With the appointment record (7 years, same as `ai_appointments`) | not deleted |
| **Audio recording** | encrypted file in `AUDIO_STORAGE_DIR` (Docker volume `consultation-audio`) | **30 days** (`AUDIO_RETENTION_DAYS`) | doctor's "Delete recording", or the purge command |
| **Transcript** (labelled turns) | `consultation_turns` | **90 days** (`TRANSCRIPT_RETENTION_DAYS`) | doctor's "Delete recording", or the purge command |
| **Approved / draft notes** (all versions) | `consultation_notes` | With the medical record (**7 years**, to be confirmed by the hospital) | never deleted by the system |
| Audit trail (who approved what, what the AI drafted vs. what was signed, deletions) | `audit_log` | 7 years | not deleted |

Days are counted from the day the recording was uploaded.

### Deleting

* **Doctor, any time:** the "Delete recording and transcript" button removes the
  audio file and the transcript. **Notes, including a signed one, are kept.**
* **Automatically:** `python -m app.db.purge_recordings` removes audio older than
  30 days and transcripts older than 90 days, and never touches notes. It is safe
  to run repeatedly.
  **Gap:** nothing runs it on a schedule yet. Until the scheduler arrives
  (Stage 3), run it daily with cron / Task Scheduler / a scheduled container.
* Every deletion writes an audit entry (who or what, why, which consultation).
  No phone number is written to the audit log; patients are identified by `auth_id`.

## Consent

* Recording cannot start, and audio or a transcript cannot be stored, unless the
  **newest** consent decision for the appointment is "yes". The server checks this
  again at upload and again when the transcript is stored.
* The patient answers on their own dashboard. For in-person or phone visits the
  doctor can record that the patient agreed verbally; this is marked
  `doctor_on_behalf`.
* **A doctor cannot overrule a refusal the patient made themselves.** Only the
  patient can change it.
* The wording shown to the patient is versioned (`v1`). The Hindi and Kannada
  texts are first drafts and need a native-speaker review before use.
* **Gap:** if a patient withdraws consent after a recording was made, the existing
  recording is **not** deleted automatically. New recording and processing stop;
  the doctor sees "declined" and should delete the recording.
* **Gap:** the web login is phone-number only (no password or OTP), so "the
  patient" is whoever knows the phone number. That is acceptable for a demo but
  not for production consent.

## Protection

* **Audio at rest:** AES-256-GCM, encrypted by the application before the file is
  written. The key is `AUDIO_ENCRYPTION_KEY` (32 random bytes, base64). Without a
  key, uploading is refused (HTTP 503); audio is never stored unencrypted. Each
  file is bound to its consultation id, so a copied file will not decrypt under
  another name, and any tampering is detected.
* **Transcripts and notes at rest:** stored as ordinary database columns. They are
  protected by the **database's own encryption at rest** (managed PostgreSQL such
  as Supabase or AWS RDS encrypts storage by default; the AWS plan in the roadmap
  specifies an encrypted RDS). **The disposable local test database is not
  encrypted.**
* **In transit:** use TLS in front of the gateway for any real deployment (the
  roadmap's Nginx layer). The local stack is plain HTTP.
* **Who can see what:** only the doctor the appointment was booked with can open
  its consultation, transcript or notes (a staff token linked to that doctor).
  Other doctors and other staff get 403. Patients cannot read clinical data. The
  doctor page can reach only an allow-list of routes.
* **Doctors see patients masked** (`Patient ••••1234`), never the full number.
* **Key management - Gap:** the key lives in an environment variable. There is no
  key rotation or re-encryption tool. Losing the key makes recordings
  unreadable; leaking it exposes them. Keep it in a secrets manager in production.
* **Backups - Gap:** deleted audio files and rows can still exist in database or
  volume backups until those backups expire (the roadmap uses 30-day RDS
  backups). Back up the audio volume with the same retention.

## Third parties

Audio clips are sent to **Sarvam AI** for speech-to-text and the transcript is
sent to Sarvam for the written note. The roadmap (Appendix E) requires a signed
**data processing agreement with Sarvam** before real patient data is used.
**Gap:** not yet in place.

## Human sign-off

Nothing the AI writes is final. A note starts as a **draft**; only the doctor can
approve it, and from then on that version is locked. Any change is saved as a new
version, and the audit log records what the AI drafted next to what the doctor
approved.
