import pytest
from unittest.mock import patch
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Scan, Review, Feedback

@pytest.fixture
async def test_scan(db_session: AsyncSession, test_business):
    scan = Scan(business_id=test_business.id, ip_hash="127.0.0.1")
    db_session.add(scan)
    await db_session.commit()
    await db_session.refresh(scan)
    return scan

@pytest.mark.asyncio
async def test_review_landing(client, test_business, db_session: AsyncSession):
    response = client.get(f"/review/{test_business.slug}")
    assert response.status_code == 200
    assert "How was your visit?" in response.text
    
    # Check if a Scan record was created
    res = await db_session.execute(select(Scan).filter(Scan.business_id == test_business.id))
    scans = res.scalars().all()
    assert len(scans) >= 1
    scan_id = str(scans[0].id)
    
    return scan_id

@pytest.mark.asyncio
async def test_review_rate_post(client, test_business, test_scan):
    response = client.post(
        f"/review/{test_business.slug}/rate",
        data={
            "rating": 5,
            "scan_id": str(test_scan.id)
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert response.status_code == 200
    assert "What stood out?" in response.text

@pytest.mark.asyncio
@patch("app.routers.review.generate_review_variations")
async def test_review_generate(mock_generate_review, client, test_business, test_scan, db_session: AsyncSession):
    # Mock the AI service
    mock_generate_review.return_value = {
        "punchy": "This is a mocked short review.",
        "detailed": "This is a mocked AI generated review.",
        "warm": "This is a mocked warm review.",
    }
    
    response = client.post(
        f"/review/{test_business.slug}/generate",
        data={
            "rating": 5,
            "scan_id": str(test_scan.id),
            "selected_chips": "Great service",
            "customer_notes": "Awesome food!"
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    
    assert response.status_code == 200
    assert "This is a mocked AI generated review." in response.text
    
    # Verify the Review was saved in DB
    res = await db_session.execute(select(Review).filter(Review.business_id == test_business.id))
    reviews = res.scalars().all()
    assert len(reviews) == 1
    assert reviews[0].rating == 5
    assert reviews[0].generated_text == "This is a mocked AI generated review."
    assert "Great service" in reviews[0].customer_notes

@pytest.mark.asyncio
async def test_review_rate_limit(client, test_business, test_scan):
    # Rate limit is set to "5/minute" in config for the generate endpoint.
    # We will hit it 6 times to trigger 429
    with patch("app.routers.review.generate_review_variations") as mock_generate:
        mock_generate.return_value = {
            "punchy": "Mock short review.",
            "detailed": "Mock detailed review.",
            "warm": "Mock warm review.",
        }
        
        for i in range(6):
            response = client.post(
                f"/review/{test_business.slug}/generate",
                data={
                    "rating": 5,
                    "scan_id": str(test_scan.id),
                    "selected_chips": "",
                    "customer_notes": ""
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if i < 5:
                assert response.status_code == 200
            else:
                assert response.status_code == 429


@pytest.mark.asyncio
async def test_copy_and_redirect_tracking(client, test_business, test_scan, db_session: AsyncSession):
    review = Review(
        business_id=test_business.id,
        scan_id=test_scan.id,
        rating=5,
        customer_notes="Friendly staff",
        generated_text="Friendly team and quick service.",
    )
    db_session.add(review)
    await db_session.commit()
    await db_session.refresh(review)

    copied = client.post(
        f"/review/{test_business.slug}/copied",
        json={"review_id": str(review.id), "final_text": "Friendly team and very quick service."},
    )
    redirected = client.post(
        f"/review/{test_business.slug}/redirected",
        json={"review_id": str(review.id)},
    )
    assert copied.status_code == 200
    assert redirected.status_code == 200

    await db_session.refresh(review)
    assert review.copied is True
    assert review.redirected is True
    assert review.final_text == "Friendly team and very quick service."
