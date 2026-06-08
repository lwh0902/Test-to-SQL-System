"""FastAPI 应用入口"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.rate_limit import limiter

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

if os.getenv("JWT_SECRET", "") in ("", "change-me-in-production", "datapilot-secret-key-change-in-prod"):
    import warnings
    warnings.warn("JWT_SECRET is not set or using default value. Set a strong secret in production.")

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.spaces import router as spaces_router
from app.api.sessions import router as sessions_router
from app.api.connections import router as connections_router

app = FastAPI(
    title="DataPilot Agent",
    description="AI 指标分析平台",
    version="1.0.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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


@app.get("/api/health")
def health():
    return {"status": "ok"}
