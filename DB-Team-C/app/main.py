from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.api.routes.sessions import router as sessions_router
from app.api.routes.appointments import router as appointments_router
from app.api.routes.escalations import router as escalations_router
from app.api.routes.audit_logs import router as audit_logs_router
from app.api.routes.auth import router as auth_router
from app.db.database import warm_up_pool
from app.services.session_store import get_session_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Establish the first Postgres connection before accepting traffic, so the
    # cold connect is not paid by the first patient to log in.
    warm_up_pool()
    yield


app = FastAPI(title="Zenvy Conversation Service", lifespan=lifespan)

app.include_router(sessions_router)
app.include_router(appointments_router)
app.include_router(escalations_router)
app.include_router(audit_logs_router)
app.include_router(auth_router)

@app.get("/healthz")
def health_check():
    redis_ok = get_session_store().ping()
    payload = {"status": "ok" if redis_ok else "degraded", "redis": redis_ok}
    if not redis_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload
