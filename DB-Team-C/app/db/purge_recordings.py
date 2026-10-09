"""Delete recordings and transcripts that have outlived the retention policy.

    python -m app.db.purge_recordings

Audio is removed after AUDIO_RETENTION_DAYS (default 30) and transcripts after
TRANSCRIPT_RETENTION_DAYS (default 90), counted from the upload. Approved notes
are never deleted. Safe to run repeatedly (a daily cron or scheduler job).
See DB-Team-C/RETENTION_POLICY.md.
"""

from app.db.database import SessionLocal
from app.services.consultation_service import purge_expired


def main() -> dict:
    db = SessionLocal()
    try:
        return purge_expired(db)
    finally:
        db.close()


if __name__ == "__main__":
    removed = main()
    print(f"Purged {removed['audio']} recording(s) and {removed['transcripts']} transcript(s).")
