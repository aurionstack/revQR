from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from fastapi.templating import Jinja2Templates
from app.config import settings
from app.main import TEMPLATES_DIR

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
PAGES = {"privacy": "Privacy policy", "terms": "Terms of service",
         "refunds": "Refunds & cancellations", "shipping": "Shipping & delivery", "contact": "Contact & support"}


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
