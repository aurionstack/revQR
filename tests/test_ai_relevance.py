import pytest

from app.services.ai import (
    _get_fallback_variations,
    _parse_customer_details,
    _validate_review_relevance,
)


pytestmark = pytest.mark.no_db


NOTES = (
    "Selected highlights: Friendly staff; Great service\n"
    "Customer's own hint: Dr Mehta explained the root canal clearly and it was painless"
)


def test_parses_tags_and_customer_hint_separately():
    highlights, hint = _parse_customer_details(NOTES)
    assert highlights == ["Friendly staff", "Great service"]
    assert hint == "Dr Mehta explained the root canal clearly and it was painless"


def test_relevance_validator_rejects_generic_reviews():
    generic = {
        "punchy": "Amazing experience and I highly recommend this place to everyone.",
        "detailed": "I had a wonderful visit. Everything was excellent and I will return.",
        "warm": "Such a lovely place with good vibes. I had a fantastic experience.",
    }
    issues = _validate_review_relevance(
        generic,
        ["Friendly staff", "Great service"],
        "Dr Mehta explained the root canal clearly and it was painless",
    )
    assert issues
    assert any("concrete hint" in issue for issue in issues)


def test_relevance_validator_accepts_fact_grounded_reviews():
    grounded = {
        "punchy": "Dr Mehta explained my root canal clearly, and the friendly staff provided great service throughout.",
        "detailed": "I received great service from the friendly staff. Dr Mehta explained the root canal clearly, and the procedure was painless.",
        "warm": "The friendly staff made me feel comfortable from the start. Dr Mehta clearly explained my root canal, which was painless, and the service was great.",
    }
    assert not _validate_review_relevance(
        grounded,
        ["Friendly staff", "Great service"],
        "Dr Mehta explained the root canal clearly and it was painless",
    )


def test_fallback_keeps_supplied_details_in_every_variation():
    variations = _get_fallback_variations(5, "Smile Dental Clinic", NOTES)
    for review in variations.values():
        lowered = review.lower()
        assert "friendly staff" in lowered
        assert "great service" in lowered
        assert "dr mehta" in lowered
        assert "root canal" in lowered
