import json


PUBLIC_PAGES = (
    ("/", "Google Review QR Code & AI Review Assistant | revQR"),
    ("/features", "Google Review QR Code Features for Local Businesses | revQR"),
    ("/pricing", "revQR Pricing — Google Review QR Codes from ₹999/year"),
    ("/about", "About RevQR — Aurion Stack"),
)
PUBLIC_CONTENT_LAST_MODIFIED = "2026-09-18"


def site_url(app_url: str) -> str:
    """Return the configured public origin without a trailing slash."""
    return app_url.rstrip("/")


def _organization(origin: str) -> dict:
    return {
        "@type": "Organization",
        "@id": f"{origin}/#organization",
        "name": "revQR",
        "url": f"{origin}/",
        "logo": {
            "@type": "ImageObject",
            "url": f"{origin}/static/revqr-logo.svg",
            "width": 220,
            "height": 58,
        },
    }


def _website(origin: str) -> dict:
    return {
        "@type": "WebSite",
        "@id": f"{origin}/#website",
        "url": f"{origin}/",
        "name": "revQR",
        "description": "Google review QR tools for local businesses.",
        "inLanguage": "en-IN",
        "publisher": {"@id": f"{origin}/#organization"},
    }


def _software_application(origin: str) -> dict:
    return {
        "@type": "SoftwareApplication",
        "@id": f"{origin}/#software",
        "name": "revQR",
        "url": f"{origin}/",
        "description": (
            "A web platform for branded Google review QR codes, customer-led "
            "AI review drafts, source tracking, and review analytics."
        ),
        "applicationCategory": "BusinessApplication",
        "applicationSubCategory": "Customer review management",
        "operatingSystem": "Any device with a web browser",
        "inLanguage": "en-IN",
        "featureList": [
            "Branded Google review QR codes",
            "Customer-led AI-assisted review drafts",
            "Multiple selectable review highlights",
            "Table, counter, staff, and location source tracking",
            "Scan and review analytics",
            "Downloadable QR and standee images",
            "Private customer feedback",
        ],
        "offers": [
            {
                "@type": "Offer",
                "name": "revQR 1 Year",
                "url": f"{origin}/pricing",
                "price": "999",
                "priceCurrency": "INR",
                "availability": "https://schema.org/InStock",
            },
            {
                "@type": "Offer",
                "name": "revQR 2 Years",
                "url": f"{origin}/pricing",
                "price": "1599",
                "priceCurrency": "INR",
                "availability": "https://schema.org/InStock",
            },
        ],
        "publisher": {"@id": f"{origin}/#organization"},
    }


def homepage_structured_data(app_url: str) -> str:
    origin = site_url(app_url)
    data = {
        "@context": "https://schema.org",
        "@graph": [
            _organization(origin),
            _website(origin),
            _software_application(origin),
        ],
    }
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def public_page_structured_data(
    app_url: str,
    *,
    path: str,
    name: str,
    description: str,
) -> str:
    origin = site_url(app_url)
    page_url = f"{origin}{path}"
    data = {
        "@context": "https://schema.org",
        "@graph": [
            _organization(origin),
            _website(origin),
            {
                "@type": "WebPage",
                "@id": f"{page_url}#webpage",
                "url": page_url,
                "name": name,
                "description": description,
                "inLanguage": "en-IN",
                "isPartOf": {"@id": f"{origin}/#website"},
                "about": {"@id": f"{origin}/#software"},
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": 1,
                        "name": "Home",
                        "item": f"{origin}/",
                    },
                    {
                        "@type": "ListItem",
                        "position": 2,
                        "name": name.split(" | ", 1)[0],
                        "item": page_url,
                    },
                ],
            },
            _software_application(origin),
        ],
    }
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def sitemap_xml(app_url: str) -> str:
    origin = site_url(app_url)
    urls = "".join(
        f"<url><loc>{origin}{path}</loc><lastmod>{PUBLIC_CONTENT_LAST_MODIFIED}</lastmod></url>"
        for path, _ in PUBLIC_PAGES
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>"
    )
