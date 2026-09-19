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
from app.services.monitoring import configure_monitoring
configure_monitoring()
from app.services.seo import (
    homepage_structured_data,
    public_page_structured_data,
    site_url,
    sitemap_xml,
)
from app.services.time import format_local_datetime

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
        if settings.JWT_ALGORITHM != "HS256":
            raise RuntimeError("JWT_ALGORITHM must be HS256.")
        if not settings.APP_URL.lower().startswith("https://"):
            raise RuntimeError("APP_URL must use HTTPS in production.")
        if not 15 <= settings.ADMIN_SESSION_MINUTES <= 120:
            raise RuntimeError("ADMIN_SESSION_MINUTES must be between 15 and 120 in production.")
        if settings.MAX_REQUEST_BYTES < settings.MAX_LOGO_BYTES:
            raise RuntimeError("MAX_REQUEST_BYTES must be at least MAX_LOGO_BYTES.")
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
    openapi_url=None if settings.ENVIRONMENT.lower() == "production" else "/openapi.json",
)


STATIC_DIR = BASE_DIR / "static"
MEDIA_DIR = BASE_DIR.parent / "media"
os.makedirs(MEDIA_DIR, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")

# Jinja2 templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

templates.env.filters["local_time"] = format_local_datetime
templates.env.globals["launch_config"] = settings


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
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.MAX_REQUEST_BYTES:
                return JSONResponse(status_code=413, content={"detail": "Request body is too large"})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header"})

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
        fetch_site = request.headers.get("sec-fetch-site", "").lower()
        if fetch_site in {"cross-site", "same-site"}:
            return JSONResponse(status_code=403, content={"detail": "Cross-origin request blocked"})

        source = request.headers.get("origin") or request.headers.get("referer")
        if source:
            source_parts = urlparse(source)
            app_parts = urlparse(settings.APP_URL)
            source_origin = f"{source_parts.scheme}://{source_parts.netloc}".lower()
            expected_origin = f"{app_parts.scheme}://{app_parts.netloc}".lower()
            request_origin = f"{request.url.scheme}://{request.url.netloc}".lower()
            if source_origin not in {expected_origin, request_origin}:
                return JSONResponse(status_code=403, content={"detail": "Cross-origin request blocked"})
        elif settings.ENVIRONMENT.lower() == "production" and fetch_site != "same-origin":
            return JSONResponse(status_code=403, content={"detail": "Request origin could not be verified"})

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-XSS-Protection", "0")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    content_security_policy = (
        "default-src 'self'; "
        "base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com https://checkout.razorpay.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https://*.razorpay.com; "
        "connect-src 'self' https://*.razorpay.com; "
        "frame-src https://*.razorpay.com; media-src 'none'; worker-src 'self' blob:; manifest-src 'self'"
    )
    if settings.ENVIRONMENT.lower() == "production":
        content_security_policy += "; upgrade-insecure-requests"
    response.headers.setdefault("Content-Security-Policy", content_security_policy)
    response.headers.setdefault("Content-Language", "en-IN")
    current_vary = response.headers.get("Vary", "")
    vary_values = {value.strip() for value in current_vary.split(",") if value.strip()}
    vary_values.update({"Origin", "Sec-Fetch-Site"})
    response.headers["Vary"] = ", ".join(sorted(vary_values))
    public_indexable_paths = {"/", "/features", "/pricing", "/about", "/robots.txt", "/sitemap.xml"}
    if request.url.path in public_indexable_paths:
        # Marketing and pricing copy changes should propagate quickly. Long-lived
        # stale responses can expose retired prices after a production update.
        response.headers.setdefault("Cache-Control", "public, max-age=60, must-revalidate")
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
        response.delete_cookie("__Host-revqr-session", path="/", secure=True, httponly=True, samesite="lax")
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
    title = "revQR Pricing — Google Review QR Codes from ₹1,599/year"
    description = (
        "Choose one year for ₹1,599 or two years for ₹2,499, with unlimited scans, "
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


@app.get("/health/ready", include_in_schema=False)
async def readiness_check():
    import asyncio
    from sqlalchemy import text
    from app.database import async_session_factory
    try:
        async with asyncio.timeout(3):
            async with async_session_factory() as db:
                await db.execute(text("SELECT 1 FROM payments LIMIT 1"))
        return {"status":"ready"}
    except Exception:
        return JSONResponse({"status":"unavailable"}, status_code=503)


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

from app.routers import public_info, launch
app.include_router(public_info.router)
app.include_router(launch.router)
