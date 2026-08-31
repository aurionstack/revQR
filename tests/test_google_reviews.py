import pytest

from app.services.google_reviews import (
    GOOGLE_BUSINESS_REVIEWS_URL,
    GoogleReviewLinkError,
    google_review_destination,
    normalize_google_review_link,
)


pytestmark = pytest.mark.no_db


def test_business_reply_destination_opens_google_review_manager():
    assert GOOGLE_BUSINESS_REVIEWS_URL == "https://business.google.com/reviews"


def test_accepts_current_g_page_review_link():
    link = "https://g.page/r/CdjQSncozrEtEAI/review"
    assert normalize_google_review_link(link) == link
    assert google_review_destination(link) == link


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


def test_normalizes_legacy_review_link():
    place_id = "ChIJN1t_tDeuEmsRUsoyG83frY4"
    submitted = (
        "https://search.google.com/local/writereview"
        f"?placeid={place_id}&source=revqr#ignored"
    )
    assert normalize_google_review_link(submitted) == (
        "https://search.google.com/local/writereview?placeid=" + place_id
    )
