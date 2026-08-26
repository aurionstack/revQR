import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Ensure static directories exist
STATIC_DIR.mkdir(parents=True, exist_ok=True)
(STATIC_DIR / "css").mkdir(exist_ok=True)
(STATIC_DIR / "js").mkdir(exist_ok=True)
(STATIC_DIR / "uploads").mkdir(exist_ok=True)


# ── App Lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup
    yield
    # Shutdown — engine disposal handled by asyncpg
    from app.database import engine
    await engine.dispose()


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="revQR",
    description="AI-powered Google review generator for SMBs",
    version="0.1.0",
    lifespan=lifespan,
)


STATIC_DIR = BASE_DIR / "static"
MEDIA_DIR = BASE_DIR.parent / "media"
os.makedirs(MEDIA_DIR, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")

# Jinja2 templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from app.services.rate_limit import limiter

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, tags=["public"])
async def root(request: Request):
    return templates.TemplateResponse(request, "landing/index.html")

@app.get("/health", tags=["system"])
async def health_check():
    return {"status": "ok", "version": "0.1.0"}


# ── Routers (will be added as we build each phase) ───────────────────────

# Phase 2:
from app.routers import auth
app.include_router(auth.router)

# Phase 3: 
from app.routers import dashboard
app.include_router(dashboard.router)

# Phase 4:
from app.routers import review
app.include_router(review.router)

from app.routers import qr
app.include_router(qr.router)

# Billing (Razorpay) — ready now
from app.routers import billing
app.include_router(billing.router)

# Super Admin Portal (Client creation, cash unlocks, account controls)
from app.routers import admin
app.include_router(admin.router)

