import pytest
from httpx import Client

@pytest.fixture
def auth_client(client, test_business):
    """Returns an authenticated client."""
    client.post(
        "/login",
        data={"email": test_business.email, "password": "password123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    yield client

@pytest.mark.asyncio
async def test_dashboard_unauthenticated(client):
    response = client.get("/dashboard")
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_dashboard_authenticated(auth_client):
    response = auth_client.get("/dashboard")
    assert response.status_code == 200
    assert "QR Code" in response.text

@pytest.mark.asyncio
async def test_qr_page_locked(auth_client):
    response = auth_client.get("/dashboard/qr")
    assert response.status_code == 200
    assert "Unlock Your QR Code" in response.text or "One-time payment" in response.text
    # Should not contain download links
    assert "/qr/download/png" not in response.text

@pytest.mark.asyncio
async def test_qr_page_unlocked(auth_client, test_business, db_session):
    # Mark business as paid
    test_business.has_paid = True
    db_session.add(test_business)
    await db_session.commit()
    
    response = auth_client.get("/dashboard/qr")
    assert response.status_code == 200
    assert "Download PNG" in response.text
