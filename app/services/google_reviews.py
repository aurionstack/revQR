"""Validation and normalization for direct Google review links."""

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


PLACE_ID_PATTERN = re.compile(r"(?:ChIJ|GhIJ|Eic|Iho)[A-Za-z0-9_-]+")
GOOGLE_BUSINESS_REVIEWS_URL = "https://business.google.com/reviews"
G_PAGE_REVIEW_PATH = re.compile(
    r"^/(?:r/)?(?P<code>[A-Za-z0-9_-]+)/review/?$",
    re.IGNORECASE,
)


class GoogleReviewLinkError(ValueError):
    """Raised when a submitted value is not a direct Google review link."""


def normalize_google_review_link(value: str | None) -> str | None:
    """
    Validate a direct Google review link without making a network/API request.

    Existing Place IDs remain accepted so older accounts continue to work, but
    new links must point directly to Google's review form.
    """
    submitted = (value or "").strip()
    if not submitted:
        return None

    if PLACE_ID_PATTERN.fullmatch(submitted):
        return submitted

    if len(submitted) > 255:
        raise GoogleReviewLinkError("The Google review link is too long.")

    parsed = urlparse(submitted)
    if parsed.scheme.lower() != "https":
        raise GoogleReviewLinkError(
            "Paste the complete Google review link beginning with https://."
        )

    host = (parsed.hostname or "").lower().rstrip(".")

    # Current Business Profile links, for example:
    # https://g.page/r/CdjQSncozrEtEAI/review
    if host == "g.page":
        match = G_PAGE_REVIEW_PATH.fullmatch(parsed.path)
        if match:
            path = f"/r/{match.group('code')}/review" if parsed.path.lower().startswith("/r/") else f"/{match.group('code')}/review"
            return urlunparse(("https", "g.page", path, "", "", ""))

    # Legacy Google Business Profile review links remain valid.
    if host == "search.google.com" and parsed.path.rstrip("/") == "/local/writereview":
        place_id = parse_qs(parsed.query).get("placeid", [""])[0].strip()
        if PLACE_ID_PATTERN.fullmatch(place_id):
            return "https://search.google.com/local/writereview?" + urlencode(
                {"placeid": place_id}
            )

    raise GoogleReviewLinkError(
        "This is not a direct Google review link. In your Google Business Profile, choose “Ask for reviews”, copy that link, and paste it here."
    )


def google_review_destination(stored_value: str | None) -> str | None:
    """Return a direct review destination for new links and legacy Place IDs."""
    value = (stored_value or "").strip()
    if not value:
        return None
    if PLACE_ID_PATTERN.fullmatch(value):
        return f"https://search.google.com/local/writereview?{urlencode({'placeid': value})}"
    return normalize_google_review_link(value)
