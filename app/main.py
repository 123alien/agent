from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.tasks import router as tasks_router
from app.api.agents import router as agents_router
from app.core.config import settings


app = FastAPI(title=settings.app_name, version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_allowed_origins) or ["http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_agent_api_token(request: Request, call_next):
    if (
        settings.agent_api_token
        and (
            request.url.path.startswith("/api/agent/")
            or request.url.path.startswith("/api/v1/agents/")
        )
        and request.headers.get("X-API-Key", "") != settings.agent_api_token
    ):
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing X-API-Key"})
    return await call_next(request)

app.include_router(tasks_router, prefix="/api/agent", tags=["agent-tasks"])
app.include_router(agents_router, prefix="/api/v1/agents", tags=["standalone-agents"])

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "api_version": "1.0.0"}

