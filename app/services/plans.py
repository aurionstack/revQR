"""Server-owned pricing and subscription entitlement rules."""

from calendar import monthrange
from datetime import datetime, timezone

from app.config import settings


def _plan_catalog() -> dict[str, dict[str, int | str]]:
    return {
        "annual": {
            "name": "1 Year",
            "months": 12,
            "amount": settings.ANNUAL_PRICE_PAISE,
        },
        "two_year": {
            "name": "2 Years",
            "months": 24,
            "amount": settings.TWO_YEAR_PRICE_PAISE,
        },
    }


def get_plan(plan_code: str | None) -> dict[str, int | str] | None:
    return _plan_catalog().get((plan_code or "").strip())


def public_plans() -> list[dict[str, int | str]]:
    plans = []
    for code, plan in _plan_catalog().items():
        amount = int(plan["amount"])
        months = int(plan["months"])
        plans.append({
            "code": code,
            **plan,
            "price_rupees": amount // 100,
            "monthly_rupees": round(amount / 100 / months),
        })
    return plans


def add_months(value: datetime, months: int) -> datetime:
    """Add calendar months while keeping the day valid."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)
