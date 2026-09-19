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
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/login")

@pytest.mark.asyncio
async def test_dashboard_authenticated(auth_client):
    response = auth_client.get("/dashboard")
    assert response.status_code == 200
    assert "QR Code" in response.text

@pytest.mark.asyncio
async def test_qr_page_locked(auth_client):
    response = auth_client.get("/dashboard/qr")
    assert response.status_code == 200
    assert "Choose your plan" in response.text
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
    assert "PNG" in response.text


@pytest.mark.asyncio
async def test_standee_uses_single_fixed_four_by_six_format(
    auth_client, test_business, db_session
):
    test_business.has_paid = True
    db_session.add(test_business)
    await db_session.commit()

    response = auth_client.get("/dashboard/standee")
    assert response.status_code == 200
    assert "4 × 6 inches" in response.text
    assert "1200 × 1800 px" in response.text
    assert "Leave a quick review!" in response.text
    assert "Takes less than 30 seconds" in response.text
    assert "15-second review" not in response.text
    assert "A4 (Wall Poster)" not in response.text
    assert "A5 (Counter)" not in response.text
    assert "A6 (Table Tent)" not in response.text
    assert "setStandeeSize" not in response.text
