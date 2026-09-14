import pytest
from types import SimpleNamespace

import app.services.ai as ai
from app.services.ai import (
    _candidate_key_indexes,
    _generate_content_with_fallback,
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


def test_fallback_does_not_invent_details_when_customer_supplies_none():
    variations = _get_fallback_variations(5, "Example Business", "")
    invented_terms = {"staff", "clean", "service", "food", "price", "fast"}
    for review in variations.values():
        assert not invented_terms.intersection(review.lower().split())


def test_key_order_rotates_and_skips_cooled_down_key(monkeypatch):
    monkeypatch.setattr(ai, "API_KEYS", ["first", "second", "third"])
    monkeypatch.setattr(ai, "_key_cursor", 0)
    monkeypatch.setattr(ai, "_key_retry_after", {0: 200.0})
    assert _candidate_key_indexes(now=100.0) == [1, 2]
    assert _candidate_key_indexes(now=100.0) == [1, 2]


@pytest.mark.asyncio
async def test_gemini_failure_uses_next_configured_key(monkeypatch):
    attempted: list[str] = []

    class FakeModels:
        def __init__(self, key: str):
            self.key = key

        async def generate_content(self, **_kwargs):
            attempted.append(self.key)
            if self.key == "failed-key":
                raise RuntimeError("provider rejected key")
            return SimpleNamespace(text="ok")

    class FakeClient:
        def __init__(self, api_key: str):
            self.aio = SimpleNamespace(models=FakeModels(api_key))

    monkeypatch.setattr(ai, "API_KEYS", ["failed-key", "working-key"])
    monkeypatch.setattr(ai, "_key_cursor", 0)
    monkeypatch.setattr(ai, "_key_retry_after", {})
    monkeypatch.setattr(ai.genai, "Client", FakeClient)

    response = await _generate_content_with_fallback("model", "prompt", None)
    assert response.text == "ok"
    assert attempted == ["failed-key", "working-key"]
