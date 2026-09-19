import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from app.models import Business
from app.services.assets import LogoValidationError, normalize_logo
from app.services.auth import issue_email_otp, validate_password_strength, verify_email_otp
from app.services.plans import add_months, get_plan, public_plans

pytestmark = pytest.mark.no_db


def make_business(**overrides):
    values = {
        "id": uuid.uuid4(),
        "name": "Test Business",
        "slug": "test-business",
        "email": "owner@example.com",
        "password_hash": "unused",
        "is_admin": False,
        "has_paid": False,
        "password_version": 0,
        "email_otp_attempts": 0,
    }
    values.update(overrides)
    return Business(**values)


def test_server_owned_plan_prices_and_discount():
    annual = get_plan("annual")
    two_year = get_plan("two_year")
    assert annual["amount"] == 159900
    assert two_year["amount"] == 249900
    assert len(public_plans()) == 2
    assert get_plan("invalid") is None


def test_calendar_subscription_extension():
    start = datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert add_months(start, 1).date().isoformat() == "2026-02-28"
    assert add_months(start, 12).date().isoformat() == "2027-01-31"


def test_subscription_expiry_is_enforced():
    active = make_business(has_paid=True, subscription_expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    expired = make_business(has_paid=True, subscription_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert active.has_active_subscription is True
    assert expired.has_active_subscription is False


def test_email_code_is_one_time_and_attempt_limited():
    business = make_business()
    code, wait = issue_email_otp(business, "email_verification", force=True)
    assert wait == 0 and len(code) == 6
    assert verify_email_otp(business, "email_verification", code)[0] is True
    assert verify_email_otp(business, "email_verification", code)[0] is False


def test_password_policy():
    assert validate_password_strength("short")
    assert validate_password_strength("alllowercase1")
    assert validate_password_strength("NoNumbersHere")
    assert validate_password_strength("StrongPass1") is None


def test_logo_is_validated_and_normalized():
    source = io.BytesIO()
    Image.new("RGBA", (128, 96), (100, 40, 200, 180)).save(source, "PNG")
    data, content_type, digest = normalize_logo(source.getvalue())
    assert content_type == "image/webp"
    assert len(digest) == 64
    with Image.open(io.BytesIO(data)) as normalized:
        assert normalized.format == "WEBP"


def test_invalid_logo_is_rejected():
    with pytest.raises(LogoValidationError):
        normalize_logo(b"not an image")
