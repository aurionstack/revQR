import json

from app.services.review_tags import suggest_review_tags


def test_tags_use_owner_business_description():
    tags = suggest_review_tags(
        5,
        business_name="Aurion Stack",
        custom_prompt="We build websites and provide SEO and app development services.",
    )
    assert tags == [
        "Design quality",
        "Technical expertise",
        "Clear communication",
        "On-time delivery",
        "SEO support",
    ]


def test_tags_use_imported_public_description():
    context = json.dumps(
        {"version": 1, "description": "A neighbourhood cafe serving coffee, pastries and breakfast."}
    )
    tags = suggest_review_tags(2, business_name="Morning Cup", scraped_context=context)
    assert tags == ["Food quality", "Wait time", "Order accuracy", "Cleanliness", "Pricing"]


def test_tags_fall_back_safely_without_recognised_context():
    assert suggest_review_tags(3, business_name="Example Company") == [
        "Service",
        "Staff",
        "Quality",
        "Wait time",
        "Value",
    ]


def test_legacy_or_invalid_scraped_context_is_ignored():
    assert "Food quality" not in suggest_review_tags(
        5,
        business_name="Example Company",
        scraped_context='{"description":"restaurant"}',
    )
