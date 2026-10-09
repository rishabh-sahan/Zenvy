from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.api.routes.sessions import router as sessions_router
from app.api.routes.appointments import router as appointments_router
from app.api.routes.escalations import router as escalations_router
from app.api.routes.audit_logs import router as audit_logs_router
from app.api.routes.authentication import router as authentication_router
from app.api.routes.doctors import router as doctors_router
from app.api.routes.consultations import router as consultations_router
from app.api.routes.prescriptions import router as prescriptions_router
from app.core.config import settings
from app.services.scheduler import scheduler
from app.services.session_store import get_session_store

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the reminder/purge scheduler with the service and stop it on shutdown.

    Plain TestClient(app) does not run this, so tests are not affected; set
    SCHEDULER_ENABLED=false to run the service without it.
    """
    if settings.SCHEDULER_ENABLED:
        scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="Zenvy Conversation Service", lifespan=lifespan)

app.include_router(sessions_router)
app.include_router(appointments_router)
app.include_router(escalations_router)
app.include_router(audit_logs_router)
app.include_router(authentication_router)
app.include_router(doctors_router)
app.include_router(consultations_router)
app.include_router(prescriptions_router)

@app.get("/healthz")
def health_check():
    redis_ok = get_session_store().ping()
    payload = {"status": "ok" if redis_ok else "degraded", "redis": redis_ok}
    if not redis_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.get("/healthz/scheduler")
def scheduler_status():
    """Is the reminder/purge job running, and what did its last pass do?"""
    return {
        "running": scheduler.running,
        "reminder_mode": settings.REMINDER_MODE,
        "poll_seconds": settings.REMINDER_POLL_SECONDS,
        "last_tick": scheduler.last_tick.isoformat() if scheduler.last_tick else None,
        "last_result": scheduler.last_result,
    }
