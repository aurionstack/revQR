from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from app.config import settings
from app.main import TEMPLATES_DIR

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
PAGES = {"about": "About RevQR", "privacy": "Privacy policy", "terms": "Terms of service",
         "refunds": "Refunds & cancellations", "shipping": "Shipping & delivery", "contact": "Contact & support"}


@router.get("/about-us", include_in_schema=False)
async def about_alias():
    return RedirectResponse("/about", status_code=308)


@router.get("/about")
@router.get("/privacy")
@router.get("/terms")
@router.get("/refunds")
@router.get("/shipping")
@router.get("/contact")
async def public_info(request: Request):
    page = request.url.path.strip("/")
    if page not in PAGES:
        raise HTTPException(404)
    return templates.TemplateResponse(request,"landing/policy.html",{
        "page":page,"heading":PAGES[page],"config":settings,
        "current_year":datetime.now(timezone.utc).year,
    })
