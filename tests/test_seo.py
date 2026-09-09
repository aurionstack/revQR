import json
import re
from pathlib import Path
from xml.etree import ElementTree

import pytest

from app.config import settings


pytestmark = pytest.mark.no_db


def extract_json_ld(html: str) -> dict:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    assert match, "JSON-LD structured data is missing"
    return json.loads(match.group(1))


def test_homepage_has_complete_search_and_social_metadata(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "Google Review QR Code &amp; AI Review Assistant | revQR" in html
    assert '<meta name="robots" content="index, follow' in html
    assert f'<link rel="canonical" href="{settings.APP_URL.rstrip("/")}/"' in html
    assert '<meta property="og:image"' in html
    assert '<meta name="twitter:card" content="summary_large_image"' in html
    assert html.count("<h1") == 1

    structured = extract_json_ld(html)
    types = {entry["@type"] for entry in structured["@graph"]}
    assert {"Organization", "WebSite", "SoftwareApplication"}.issubset(types)
    software = next(entry for entry in structured["@graph"] if entry["@type"] == "SoftwareApplication")
    assert {offer["price"] for offer in software["offers"]} == {"999", "1599"}


@pytest.mark.parametrize(
    ("path", "canonical_suffix", "expected_phrase"),
    [
        ("/features", "/features", "A complete Google review QR code workflow"),
        ("/pricing", "/pricing", "Two straightforward plans"),
    ],
)
def test_public_marketing_pages_are_unique_and_indexable(client, path, canonical_suffix, expected_phrase):
    response = client.get(path)
    assert response.status_code == 200
    assert expected_phrase in response.text
    assert '<meta name="robots" content="index, follow' in response.text
    assert f'<link rel="canonical" href="{settings.APP_URL.rstrip("/")}{canonical_suffix}"' in response.text
    assert response.headers.get("x-robots-tag") is None


def test_sitemap_and_robots_expose_only_public_marketing_pages(client):
    sitemap_response = client.get("/sitemap.xml")
    assert sitemap_response.status_code == 200
    assert sitemap_response.headers["content-type"].startswith("application/xml")
    root = ElementTree.fromstring(sitemap_response.text)
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locations = [element.text for element in root.findall("sm:url/sm:loc", namespace)]
    origin = settings.APP_URL.rstrip("/")
    assert locations == [f"{origin}/", f"{origin}/features", f"{origin}/pricing"]

    robots_response = client.get("/robots.txt")
    assert robots_response.status_code == 200
    assert f"Sitemap: {origin}/sitemap.xml" in robots_response.text
    assert "Disallow: /dashboard" in robots_response.text
    assert "Disallow: /login" not in robots_response.text


def test_non_marketing_pages_are_noindex_and_not_cached(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert '<meta name="robots" content="noindex, nofollow, noarchive"' in response.text
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert response.headers["cache-control"] == "no-store"


def test_social_and_brand_images_have_production_dimensions():
    from PIL import Image

    static_dir = Path(__file__).parents[1] / "app" / "static"
    with Image.open(static_dir / "og-revqr.png") as image:
        assert image.format == "PNG"
        assert image.size == (1200, 630)

    with Image.open(static_dir / "brand-icon.png") as image:
        assert image.format == "PNG"
        assert image.size == (512, 512)


def test_marketing_page_avoids_unused_application_javascript(client):
    response = client.get("/")

    assert "unpkg.com/htmx" not in response.text
    assert "/static/js/app.js" not in response.text


def test_static_assets_receive_long_lived_cache_headers(client):
    response = client.get("/static/og-revqr.png")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_production_request_redirects_to_https_canonical_domain(client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "APP_URL", "https://revqr.tech")

    response = client.get(
        "/pricing?utm_source=seo",
        headers={"host": "www.revqr.tech", "x-forwarded-proto": "http"},
        follow_redirects=False,
    )

    assert response.status_code == 308
    assert response.headers["location"] == "https://revqr.tech/pricing?utm_source=seo"
