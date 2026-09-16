import json
import httpx
import pytest
from app.services import business_context as context

pytestmark = pytest.mark.no_db


def test_extracts_only_matching_business_description():
    html = '<script type="application/ld+json">' + json.dumps({"@graph": [
        {"@type": "Restaurant", "name": "Wrong Cafe", "description": "Wrong business"},
        {"@type": "Restaurant", "name": "My Cafe", "description": "Coffee and fresh pastries."},
    ]}) + '</script>'
    assert context.extract_description(html, "My Cafe") == "Coffee and fresh pastries."
    assert context.extract_description(html, "Another Cafe") is None


def test_never_imports_customer_review_as_description():
    html='<script type="application/ld+json">{"@type":"Review","name":"My Cafe","description":"Five stars!"}</script>'
    assert context.extract_description(html, "My Cafe") is None


@pytest.mark.asyncio
async def test_redirect_cannot_fetch_arbitrary_host(monkeypatch):
    visited=[]
    def handler(request):
        visited.append(str(request.url))
        return httpx.Response(302,headers={"location":"https://127.0.0.1/private"})
    factory=httpx.AsyncClient
    monkeypatch.setattr(context.httpx,"AsyncClient",lambda **kw:factory(transport=httpx.MockTransport(handler),**kw))
    result=json.loads(await context.import_business_context("https://g.page/r/CdjQSncozrEtEAI/review","My Cafe"))
    assert result["description"] is None
    assert len(visited)==1


def test_legacy_mock_context_is_not_trusted():
    assert not context._allowed("https://www.google.com.evil.test/")
    assert not context._allowed("http://www.google.com/")
    assert not context._allowed("https://user:pass@www.google.com/")


@pytest.mark.asyncio
async def test_import_stores_description_and_provenance(monkeypatch):
    html='<script type="application/ld+json">{"@type":"CafeOrCoffeeShop","name":"My Cafe","description":"Coffee and pastries."}</script>'
    factory=httpx.AsyncClient
    monkeypatch.setattr(context.httpx,"AsyncClient",lambda **kw:factory(transport=httpx.MockTransport(lambda request:httpx.Response(200,text=html,headers={"content-type":"text/html"})),**kw))
    record=json.loads(await context.import_business_context("https://g.page/r/CdjQSncozrEtEAI/review","My Cafe"))
    assert record["status"]=="imported"
    assert record["description"]=="Coffee and pastries."
    assert record["source"].startswith("https://g.page/")
