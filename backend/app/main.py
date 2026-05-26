"""FastAPI 应用入口"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.spaces import router as spaces_router
from app.api.sessions import router as sessions_router

app = FastAPI(
    title="DataPilot Agent",
    description="AI 指标分析平台",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(spaces_router)
app.include_router(sessions_router)
app.include_router(chat_router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
