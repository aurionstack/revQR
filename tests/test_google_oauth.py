from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Business
from app.routers import auth


@pytest.fixture
def google_enabled(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setitem(auth.templates.env.globals, "google_oauth_enabled", True)


def begin_google_flow(client, mode="login"):
    response = client.get(f"/auth/google?mode={mode}", follow_redirects=False)
    assert response.status_code == 302
    parsed = urlparse(response.headers["location"])
    assert parsed.netloc == "accounts.google.com"
    query = parse_qs(parsed.query)
    assert query["scope"] == ["openid email profile"]
    assert query["redirect_uri"] == [f"{settings.APP_URL.rstrip('/')}/auth/google/callback"]
    return query["state"][0]


@pytest.mark.asyncio
async def test_google_signup_creates_verified_account(client, db_session, monkeypatch, google_enabled):
    state = begin_google_flow(client, "signup")

    async def exchange(code, nonce):
        assert code == "valid-code"
        assert nonce
        return {
            "sub": "google-user-123",
            "email": "owner@example.com",
            "email_verified": True,
            "name": "Owner Name",
        }

    monkeypatch.setattr(auth, "exchange_google_code", exchange)
    response = client.get(
        f"/auth/google/callback?code=valid-code&state={state}",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard"

    account = (await db_session.execute(select(Business).where(Business.email == "owner@example.com"))).scalar_one()
    assert account.google_subject == "google-user-123"
    assert account.email_verified is True


@pytest.mark.asyncio
async def test_google_login_links_matching_verified_email(client, db_session, test_business, monkeypatch, google_enabled):
    state = begin_google_flow(client)

    async def exchange(code, nonce):
        return {
            "sub": "linked-google-user",
            "email": test_business.email,
            "email_verified": True,
            "name": "Ignored Name",
        }

    monkeypatch.setattr(auth, "exchange_google_code", exchange)
    response = client.get(
        f"/auth/google/callback?code=valid-code&state={state}",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard"
    await db_session.refresh(test_business)
    assert test_business.google_subject == "linked-google-user"


def test_google_callback_rejects_state_without_matching_cookie(client, google_enabled):
    response = client.get(
        "/auth/google/callback?code=attacker-code&state=invalid",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/login?oauth=invalid"


def test_auth_pages_show_google_option_when_configured(client, google_enabled):
    assert "Continue with Google" in client.get("/login").text
    assert "Continue with Google" in client.get("/signup").text
