from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal, create_schema, engine
from app.middleware import RequestIDMiddleware, SecurityHeadersMiddleware
from app.routers import admin, auth, bookings, pages, simulations, teams, vpn
from app.schemas import HealthResponse
from app.services import seed_admin

APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    create_schema()
    with SessionLocal() as db:
        seed_admin(db, settings)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=APP_VERSION,
        description=(
            "Competition portal for team registration, Admin approval, login with 2FA, "
            "time-slot booking, time-based VPN access, and drone simulation control."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIDMiddleware)

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(pages.router)
    app.include_router(auth.router)
    app.include_router(teams.router)
    app.include_router(bookings.router)
    app.include_router(vpn.router)
    app.include_router(simulations.router)
    app.include_router(admin.router)

    @app.get("/healthz", response_model=HealthResponse, tags=["Operations"])
    def healthz() -> HealthResponse:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return HealthResponse(service=settings.app_name, version=APP_VERSION)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        wants_html = "text/html" in request.headers.get("accept", "") and not request.url.path.startswith("/api/")
        if wants_html:
            return pages.render(
                request,
                "error.html",
                {"user": None, "status_code": exc.status_code, "detail": exc.detail},
                settings,
                status_code=exc.status_code,
            )
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app


app = create_app()
