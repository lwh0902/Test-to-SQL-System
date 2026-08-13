"""FastAPI 应用入口"""

import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.rate_limit import limiter
from app.core.config import validate_security_config

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

if os.getenv("JWT_SECRET", "") in ("", "change-me-in-production", "datapilot-secret-key-change-in-prod"):
    import warnings
    warnings.warn("JWT_SECRET is not set or using default value. Set a strong secret in production.")

validate_security_config()

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.spaces import router as spaces_router
from app.api.sessions import router as sessions_router
from app.api.connections import router as connections_router
from app.api.diagnosis import router as diagnosis_router
from app.api.exports import router as exports_router
from app.api.pilot import router as pilot_router
from app.api.catalog import router as catalog_router

app = FastAPI(
    title="DataPilot Agent",
    description="AI 指标分析平台",
    version="1.0.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    try:
        response = await call_next(request)
    except Exception:
        return JSONResponse(status_code=500, content={"code": "INTERNAL_ERROR", "message": "服务内部错误", "request_id": request_id}, headers={"X-Request-ID": request_id})
    response.headers["X-Request-ID"] = request_id
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(spaces_router)
app.include_router(sessions_router)
app.include_router(chat_router)
app.include_router(connections_router)
app.include_router(diagnosis_router)
app.include_router(exports_router)
app.include_router(pilot_router)
app.include_router(catalog_router)

# Optional A2A lease worker (outbox retry). Enable with A2A_WORKER_ENABLED=true
_a2a_worker = None


@app.on_event("startup")
async def _start_a2a_worker():
    global _a2a_worker
    if os.getenv("A2A_WORKER_ENABLED", "false").lower() not in ("1", "true", "yes"):
        return
    try:
        import app.agents.deep_diagnosis  # noqa: F401 — register handlers
        from app.a2a.worker import A2AWorker

        _a2a_worker = A2AWorker()
        _a2a_worker.start_background()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("failed to start A2A worker")


@app.on_event("shutdown")
async def _stop_a2a_worker():
    global _a2a_worker
    if _a2a_worker is not None:
        await _a2a_worker.stop()
        _a2a_worker = None


@app.get("/api/health")
def health():
    return {"status": "ok"}
