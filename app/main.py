import os
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import settings
from app.services.seo import (
    homepage_structured_data,
    public_page_structured_data,
    site_url,
    sitemap_xml,
)

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
    if settings.ENVIRONMENT.lower() == "production":
        if settings.JWT_SECRET_KEY == "change-me-in-production" or len(settings.JWT_SECRET_KEY) < 32:
            raise RuntimeError("JWT_SECRET_KEY must be a unique value of at least 32 characters in production.")
        if not settings.SMTP_HOST or not settings.SMTP_USER or not settings.SMTP_PASSWORD:
            logging.warning("SMTP is not fully configured; email verification and recovery cannot deliver codes.")
    # Startup — ensure database tables and seed default super admin
    try:
        from create_admin import create_or_update_admin
        await create_or_update_admin()
    except Exception as e:
        print(f"[Warning] Auto-admin seed skipped: {e}")
    yield
    # Shutdown — engine disposal handled by asyncpg
    from app.database import engine
    await engine.dispose()


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="revQR",
    description="AI-powered Google review generator for SMBs",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if settings.ENVIRONMENT.lower() == "production" else "/docs",
    redoc_url=None if settings.ENVIRONMENT.lower() == "production" else "/redoc",
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
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi import status

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Block cross-origin state changes and apply baseline browser protections."""
    if settings.ENVIRONMENT.lower() == "production":
        canonical = urlparse(settings.APP_URL)
        canonical_host = canonical.hostname
        request_host = request.headers.get("host", "").split(":", 1)[0].lower()
        forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",", 1)[0].strip()
        if canonical_host and request.method in {"GET", "HEAD"} and (
            forwarded_proto != "https" or request_host != canonical_host
        ):
            query = f"?{request.url.query}" if request.url.query else ""
            return RedirectResponse(
                url=f"https://{canonical_host}{request.url.path}{query}",
                status_code=status.HTTP_308_PERMANENT_REDIRECT,
            )

    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path != "/billing/webhook":
        source = request.headers.get("origin") or request.headers.get("referer")
        if source:
            source_parts = urlparse(source)
            app_parts = urlparse(settings.APP_URL)
            source_origin = f"{source_parts.scheme}://{source_parts.netloc}".lower()
            expected_origin = f"{app_parts.scheme}://{app_parts.netloc}".lower()
            request_origin = f"{request.url.scheme}://{request.url.netloc}".lower()
            if source_origin not in {expected_origin, request_origin}:
                return JSONResponse(status_code=403, content={"detail": "Cross-origin request blocked"})

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Language", "en-IN")
    public_indexable_paths = {"/", "/features", "/pricing", "/robots.txt", "/sitemap.xml"}
    if request.url.path in public_indexable_paths:
        response.headers.setdefault("Cache-Control", "public, max-age=300, stale-while-revalidate=86400")
    elif request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    elif not request.url.path.startswith("/static/"):
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
        response.headers.setdefault("Cache-Control", "no-store")
    if settings.cookie_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

@app.exception_handler(FastAPIHTTPException)
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    accept = request.headers.get("accept", "")
    path = request.url.path
    is_html_request = "text/html" in accept or request.headers.get("sec-fetch-dest") in ["document", "empty"]
    is_protected_web_path = path.startswith("/dashboard") or path.startswith("/admin")

    if exc.status_code == 401 and (is_html_request or is_protected_web_path):
        response = RedirectResponse(url=f"/login?next={path}", status_code=status.HTTP_302_FOUND)
        response.delete_cookie("access_token")
        return response

    if exc.status_code == 403 and (is_html_request or is_protected_web_path):
        if path.startswith("/admin"):
            return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, tags=["public"])
async def root(request: Request):
    return templates.TemplateResponse(request, "landing/index.html", {
        "site_url": site_url(settings.APP_URL),
        "current_year": datetime.now(timezone.utc).year,
        "structured_data": homepage_structured_data(settings.APP_URL),
    })


@app.get("/features", response_class=HTMLResponse, tags=["public"])
async def features(request: Request):
    title = "Google Review QR Code Features for Local Businesses | revQR"
    description = (
        "Branded QR codes, relevant AI-assisted drafts, source tracking, analytics, "
        "private feedback, and downloadable standees."
    )
    return templates.TemplateResponse(request, "landing/features.html", {
        "site_url": site_url(settings.APP_URL),
        "current_year": datetime.now(timezone.utc).year,
        "structured_data": public_page_structured_data(
            settings.APP_URL, path="/features", name=title, description=description
        ),
    })


@app.get("/pricing", response_class=HTMLResponse, tags=["public"])
async def pricing(request: Request):
    title = "revQR Pricing — Google Review QR Codes from ₹999/year"
    description = (
        "Choose one year for ₹999 or two years for ₹1,599, with unlimited scans, "
        "AI-assisted drafts, analytics, and QR downloads."
    )
    return templates.TemplateResponse(request, "landing/pricing.html", {
        "site_url": site_url(settings.APP_URL),
        "current_year": datetime.now(timezone.utc).year,
        "structured_data": public_page_structured_data(
            settings.APP_URL, path="/pricing", name=title, description=description
        ),
    })


@app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots_txt():
    origin = site_url(settings.APP_URL)
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin",
        "Disallow: /dashboard",
        "Disallow: /billing",
        "Disallow: /qr",
        f"Sitemap: {origin}/sitemap.xml",
        "",
    ])
    return PlainTextResponse(body)


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    return Response(sitemap_xml(settings.APP_URL), media_type="application/xml")

@app.get("/health", tags=["system"])
async def health_check():
    return {"status": "ok", "version": "1.0.0"}


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

from app.routers import assets
app.include_router(assets.router)

# Super Admin Portal (Client creation, cash unlocks, account controls)
from app.routers import admin
app.include_router(admin.router)
