"""The background job: sends due reminders and removes expired recordings.

One small thread inside the Team-C service. Every REMINDER_POLL_SECONDS it:

* sends the reminders that are due (reminder_service.process_due), and
* about once a day runs the retention purge (consultation_service.purge_expired).

It is safe to run several copies: each reminder is claimed with a single
conditional UPDATE, so only one copy ever sends it; the purge is idempotent.
Errors are logged and never stop the loop.
"""
import logging
import threading
from datetime import datetime, timedelta

from app.core.config import settings
from app.db.database import SessionLocal
from app.services import consultation_service, reminder_service
from app.services.slot_service import utcnow

log = logging.getLogger("zenvy.scheduler")


class Scheduler:
    def __init__(self, session_factory=SessionLocal):
        self._session_factory = session_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_purge: datetime | None = None
        self.last_tick: datetime | None = None
        self.last_result: dict = {}

    # -- one pass ----------------------------------------------------------
    def tick(self, now: datetime | None = None) -> dict:
        """Do one pass of the work. Never raises."""
        now = now or utcnow()
        result: dict = {"reminders": {}, "purged": None}
        db = self._session_factory()
        try:
            try:
                result["reminders"] = reminder_service.process_due(db, now)
            except Exception:  # noqa: BLE001
                log.exception("Sending reminders failed")
                db.rollback()

            due_for_purge = (
                self._last_purge is None
                or now - self._last_purge >= timedelta(hours=settings.PURGE_INTERVAL_HOURS)
            )
            if due_for_purge:
                try:
                    result["purged"] = consultation_service.purge_expired(db, now)
                    self._last_purge = now
                    if any(result["purged"].values()):
                        log.info("Retention purge removed %s", result["purged"])
                except Exception:  # noqa: BLE001
                    log.exception("Retention purge failed")
                    db.rollback()
        finally:
            db.close()
        self.last_tick, self.last_result = now, result
        return result

    # -- the thread --------------------------------------------------------
    def _run(self) -> None:
        log.info("Scheduler started (every %s s, mode=%s)", settings.REMINDER_POLL_SECONDS, settings.REMINDER_MODE)
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(settings.REMINDER_POLL_SECONDS)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="zenvy-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


scheduler = Scheduler()
