from types import SimpleNamespace

import pytest

from app.services.google_reviews import (
    GOOGLE_BUSINESS_REVIEWS_URL,
    GoogleReviewLinkError,
    google_business_profile_destination,
    google_business_reviews_destination,
    google_local_reviews_destination,
    google_review_destination,
    normalize_google_review_link,
)


pytestmark = pytest.mark.no_db


def test_business_reply_destination_opens_google_review_manager():
    assert GOOGLE_BUSINESS_REVIEWS_URL == "https://business.google.com/reviews"


def test_place_id_opens_the_dedicated_google_review_section():
    place_id = "ChIJfwvFyLiLVkAR2NBKdyjOsS0"
    assert google_local_reviews_destination(place_id) == (
        "https://search.google.com/local/reviews?placeid=" + place_id
    )


@pytest.mark.asyncio
async def test_g_page_link_resolves_to_the_exact_review_section(monkeypatch):
    place_id = "ChIJfwvFyLiLVkAR2NBKdyjOsS0"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def head(self, _url):
            return SimpleNamespace(
                url=f"https://search.google.com/local/writereview?placeid={place_id}"
            )

    monkeypatch.setattr(
        "app.services.google_reviews.httpx.AsyncClient",
        lambda **_kwargs: FakeClient(),
    )

    destination = await google_business_reviews_destination(
        "https://g.page/r/CdjQSncozrEtEAI/review",
        "Aurion Stack",
    )
    assert destination == (
        "https://search.google.com/local/reviews?placeid=" + place_id
    )


def test_accepts_current_g_page_review_link():
    link = "https://g.page/r/CdjQSncozrEtEAI/review"
    assert normalize_google_review_link(link) == link
    assert google_review_destination(link) == link
    assert google_business_profile_destination(link, "Aurion Stack") == (
        "https://www.google.com/search?q=Aurion+Stack"
        "&ludocid=3292639475779948760&ibp=gwp%3B0%2C7"
    )


@pytest.mark.parametrize(
    "link",
    [
        "https://share.google/iWZDhURjDZeBxDAop",
        "https://g.page/r/CdjQSncozrEtEAI",
        "http://g.page/r/CdjQSncozrEtEAI/review",
        "https://example.com/review",
    ],
)
def test_rejects_profile_and_non_google_links(link):
    with pytest.raises(GoogleReviewLinkError):
        normalize_google_review_link(link)


def test_keeps_legacy_place_ids_working():
    place_id = "ChIJN1t_tDeuEmsRUsoyG83frY4"
    assert normalize_google_review_link(place_id) == place_id
    assert google_review_destination(place_id) == (
        "https://search.google.com/local/writereview?placeid=" + place_id
    )
    assert google_business_profile_destination(place_id, "Aurion Stack") == (
        "https://www.google.com/search?q=Aurion+Stack&ibp=gwp%3B0%2C7"
    )


def test_normalizes_legacy_review_link():
    place_id = "ChIJN1t_tDeuEmsRUsoyG83frY4"
    submitted = (
        "https://search.google.com/local/writereview"
        f"?placeid={place_id}&source=revqr#ignored"
    )
    assert normalize_google_review_link(submitted) == (
        "https://search.google.com/local/writereview?placeid=" + place_id
    )
