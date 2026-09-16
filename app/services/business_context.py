"""Best-effort, bounded public Google metadata import; never scrape reviews."""
import json
import asyncio
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from app.services.google_reviews import google_review_destination

GOOGLE_HOSTS = {"g.page", "www.google.com", "search.google.com", "maps.google.com"}
MAX_BYTES = 256 * 1024


def _allowed(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.scheme == "https" and parsed.hostname in GOOGLE_HOSTS
            and parsed.port in (None, 443) and not parsed.username and not parsed.password)


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.blocks = []
        self.collect = False
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key:
                self.meta[key.lower()] = attrs.get("content", "")
        if tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self.collect = True
            self.buffer = []

    def handle_data(self, data):
        if self.collect:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.collect:
            self.blocks.append("".join(self.buffer))
            self.collect = False


def _same_business(name: str, expected: str) -> bool:
    normalize = lambda value: " ".join(re.findall(r"\w+", value.casefold()))
    return normalize(name) == normalize(expected)


def extract_description(html: str, business_name: str) -> str | None:
    parser = MetadataParser()
    parser.feed(html)

    def visit(value):
        if isinstance(value, list):
            for item in value:
                found = visit(item)
                if found:
                    return found
        elif isinstance(value, dict):
            # Never import Review/AggregateRating bodies as business description.
            types = value.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if not {"Review", "AggregateRating"}.intersection(types):
                if _same_business(str(value.get("name", "")), business_name):
                    description = value.get("description")
                    if isinstance(description, str) and description.strip():
                        return " ".join(description.split())[:1500]
            return visit(value.get("@graph", []))
        return None

    for block in parser.blocks:
        try:
            found = visit(json.loads(block))
            if found:
                return found
        except (ValueError, TypeError):
            continue
    if _same_business(parser.meta.get("og:title", ""), business_name):
        description = parser.meta.get("og:description") or parser.meta.get("description")
        if description:
            return " ".join(description.split())[:1500]
    return None


async def _import_business_context(stored_link: str | None, business_name: str) -> str:
    record = {"version": 1, "description": None, "status": "unavailable",
              "fetched_at": datetime.now(timezone.utc).isoformat()}
    try:
        url = google_review_destination(stored_link)
        if not url:
            return json.dumps(record)
        async with httpx.AsyncClient(timeout=5, follow_redirects=False,
                                     headers={"User-Agent": "RevQR/1.0"}) as client:
            for _ in range(4):
                if not _allowed(url):
                    break
                async with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    if response.status_code != 200 or "text/html" not in response.headers.get("content-type", ""):
                        break
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_BYTES:
                            return json.dumps(record)
                    description = extract_description(raw.decode("utf-8", errors="replace"), business_name)
                    record.update(description=description, source=url,
                                  status="imported" if description else "unavailable")
                    break
    except (httpx.HTTPError, ValueError):
        pass
    return json.dumps(record, ensure_ascii=False)


async def import_business_context(stored_link: str | None, business_name: str) -> str:
    try:
        async with asyncio.timeout(8):
            return await _import_business_context(stored_link, business_name)
    except TimeoutError:
        return json.dumps({"version": 1, "description": None, "status": "unavailable",
                           "fetched_at": datetime.now(timezone.utc).isoformat()})
