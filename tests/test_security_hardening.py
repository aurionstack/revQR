from datetime import datetime, timezone
from pathlib import Path

import jwt
import pytest

from app.config import settings
from app.services.auth import (
    TOKEN_ALGORITHM,
    TOKEN_AUDIENCE,
    TOKEN_ISSUER,
    _decode_token,
    create_access_token,
    get_password_hash,
    verify_password,
)
from app.services.time import as_local_datetime


pytestmark = pytest.mark.no_db


def test_access_token_has_strict_identity_and_lifecycle_claims():
    token = create_access_token({"sub": "9aa37b2d-9637-4e30-b9e0-e370107e9984"})
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[TOKEN_ALGORITHM],
        audience=TOKEN_AUDIENCE,
        issuer=TOKEN_ISSUER,
    )
    assert payload["type"] == "access"
    assert all(claim in payload for claim in ("exp", "iat", "nbf", "jti", "iss", "aud"))


def test_token_missing_required_claim_is_rejected():
    token = jwt.encode(
        {"sub": "business-id", "type": "access"},
        settings.JWT_SECRET_KEY,
        algorithm=TOKEN_ALGORITHM,
    )
    with pytest.raises(jwt.PyJWTError):
        _decode_token(token, "access")


def test_new_passwords_use_argon2_and_still_verify():
    password_hash = get_password_hash("A-strong-password-123")
    assert password_hash.startswith("$argon2")
    assert verify_password("A-strong-password-123", password_hash)


def test_utc_timestamp_is_displayed_in_configured_india_timezone():
    converted = as_local_datetime(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc))
    assert converted is not None
    assert converted.strftime("%Y-%m-%d %H:%M %z") == "2026-01-01 05:30 +0530"


def test_security_headers_are_present(client):
    response = client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_cross_site_state_change_is_blocked_in_production(client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "APP_URL", "https://testserver")
    response = client.post(
        "/logout",
        headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://attacker.example"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_oversized_request_is_rejected_before_route_processing(client):
    response = client.post(
        "/logout",
        headers={"Content-Length": str(settings.MAX_REQUEST_BYTES + 1)},
        follow_redirects=False,
    )
    assert response.status_code == 413


def test_whatsapp_json_uses_context_aware_escaping():
    source = Path("app/templates/dashboard/whatsapp.html").read_text(encoding="utf-8")
    assert "wa_templates | tojson" in source
    assert "wa_templates_json | safe" not in source
