"""Validation and normalization for direct Google review links."""

import base64
import binascii
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx


PLACE_ID_PATTERN = re.compile(r"(?:ChIJ|GhIJ|Eic|Iho)[A-Za-z0-9_-]+")
GOOGLE_BUSINESS_REVIEWS_URL = "https://business.google.com/reviews"
GOOGLE_LOCAL_REVIEWS_URL = "https://search.google.com/local/reviews"
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


def google_local_reviews_destination(place_id: str | None) -> str | None:
    """Build Google's dedicated review-list URL for one exact business."""
    value = (place_id or "").strip()
    if not PLACE_ID_PATTERN.fullmatch(value):
        return None
    return GOOGLE_LOCAL_REVIEWS_URL + "?" + urlencode({"placeid": value})


def _place_id_from_google_url(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.hostname not in {"search.google.com", "www.google.com"}:
        return None
    place_id = parse_qs(parsed.query).get("placeid", [""])[0].strip()
    return place_id if PLACE_ID_PATTERN.fullmatch(place_id) else None


async def google_business_reviews_destination(
    stored_value: str | None,
    business_name: str | None = None,
) -> str:
    """Resolve a stored review link to this business's Google review list."""
    value = (stored_value or "").strip()

    direct_destination = google_local_reviews_destination(value)
    if direct_destination:
        return direct_destination

    try:
        normalized = normalize_google_review_link(value)
    except GoogleReviewLinkError:
        normalized = None

    if normalized:
        place_id = _place_id_from_google_url(normalized)
        if place_id:
            return google_local_reviews_destination(place_id) or GOOGLE_BUSINESS_REVIEWS_URL

        if urlparse(normalized).hostname == "g.page":
            try:
                async with httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=8.0,
                    headers={"User-Agent": "revQR/1.0"},
                ) as client:
                    response = await client.head(normalized)
                place_id = _place_id_from_google_url(str(response.url))
                if place_id:
                    return google_local_reviews_destination(place_id) or GOOGLE_BUSINESS_REVIEWS_URL
            except httpx.HTTPError:
                pass

    return (
        google_business_profile_destination(value, business_name)
        or GOOGLE_BUSINESS_REVIEWS_URL
    )


def _g_page_ludocid(code: str) -> int | None:
    """Decode the public location ID embedded in a current g.page code."""
    try:
        raw = base64.urlsafe_b64decode(code + "=" * (-len(code) % 4))
    except (binascii.Error, ValueError):
        return None

    # Current g.page codes contain protobuf field 1 as a fixed64 value:
    # tag 0x09 followed by the little-endian Google location/CID value.
    if len(raw) < 9 or raw[0] != 0x09:
        return None
    return int.from_bytes(raw[1:9], byteorder="little", signed=False)


def _google_search_profile_url(
    business_name: str | None,
    ludocid: int | None = None,
) -> str | None:
    params: dict[str, str] = {}
    if business_name and business_name.strip():
        params["q"] = business_name.strip()
    if ludocid is not None:
        params["ludocid"] = str(ludocid)
    if not params:
        return None
    params["ibp"] = "gwp;0,7"
    return "https://www.google.com/search?" + urlencode(params)


def google_business_profile_destination(
    stored_value: str | None,
    business_name: str | None = None,
) -> str | None:
    """Return this business's Google Search profile/reviews destination."""
    value = (stored_value or "").strip()
    if not value:
        return _google_search_profile_url(business_name)

    if PLACE_ID_PATTERN.fullmatch(value):
        # A Place ID cannot be converted to a Search ludocid offline. Use the
        # connected account's business name on Google Search, never Maps.
        return _google_search_profile_url(business_name)

    normalized = normalize_google_review_link(value)
    parsed = urlparse(normalized)

    if parsed.hostname == "g.page":
        match = G_PAGE_REVIEW_PATH.fullmatch(parsed.path)
        if match:
            destination = _google_search_profile_url(
                business_name,
                _g_page_ludocid(match.group("code")),
            )
            if destination:
                return destination

    return _google_search_profile_url(business_name)
