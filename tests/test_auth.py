import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_signup(client, db_session):
    response = client.post(
        "/signup",
        data={
            "name": "New Business",
            "email": "new@example.com",
            "password": "strongpassword123",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False
    )
    # The endpoint should return a 302 Redirect (FastAPI returns 302 for RedirectResponse if status_code=302)
    assert response.status_code in (302, 303)
    
    # Try duplicate signup
    response2 = client.post(
        "/signup",
        data={
            "name": "New Business",
            "email": "new@example.com",
            "password": "strongpassword123",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False
    )
    assert "already registered" in response2.text or response2.status_code == 400

@pytest.mark.asyncio
async def test_login(client, test_business):
    response = client.post(
        "/login",
        data={
            "email": test_business.email,
            "password": "password123",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False
    )
    # Check that it returns 302
    assert response.status_code in (302, 303)
    assert "/dashboard" in response.headers.get("location", "")
    
    # Check if cookie is set
    assert "access_token" in response.cookies

@pytest.mark.asyncio
async def test_login_invalid_password(client, test_business):
    response = client.post(
        "/login",
        data={
            "email": test_business.email,
            "password": "wrongpassword",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert "Incorrect email or password" in response.text
    assert "access_token" not in response.cookies

@pytest.mark.asyncio
async def test_logout(client, test_business):
    # First login
    client.post(
        "/login",
        data={"email": test_business.email, "password": "password123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False
    )
    # Then logout
    response = client.get("/logout", follow_redirects=False)
    
    # Cookie should be cleared
    assert "access_token=" in response.headers.get("set-cookie", "")
    assert 'max-age=0' in response.headers.get("set-cookie", "").lower() or 'expires=' in response.headers.get("set-cookie", "").lower()
