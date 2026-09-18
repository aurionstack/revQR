import asyncio
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import select, func
from app.models import UsageCounter, Notification
from app.services.usage import _reserve
from app.maintenance import enqueue_renewals, deliver_notifications
from app.services.monitoring import scrub_event


@pytest.mark.asyncio
async def test_shared_counter_is_atomic(db_session):
    factory=async_sessionmaker(db_session.bind,expire_on_commit=False)
    key="concurrency:"+uuid.uuid4().hex
    async def request():
        async with factory() as db:
            ok=await _reserve(db,key,3)
            await db.commit()
            return ok
    results=await asyncio.gather(*(request() for _ in range(8)))
    assert sum(results)==3
    assert (await db_session.get(UsageCounter,key)).count==3


@pytest.mark.asyncio
async def test_renewal_queue_is_deduplicated(test_business,db_session):
    test_business.has_paid=True
    test_business.subscription_expires_at=datetime.now(timezone.utc)+timedelta(days=5)
    await db_session.commit()
    await enqueue_renewals(db_session)
    await enqueue_renewals(db_session)
    assert (await db_session.execute(select(func.count(Notification.id)))).scalar()==1


@pytest.mark.asyncio
async def test_failed_email_is_not_marked_sent(db_session,monkeypatch):
    row=Notification(dedupe_key="test-failed",recipient="owner@example.com",subject="Receipt",message="Test")
    db_session.add(row);await db_session.commit()
    async def fails(*args):return False
    monkeypatch.setattr("app.maintenance._send_email",fails)
    await deliver_notifications(db_session)
    await db_session.refresh(row)
    assert row.sent_at is None and row.attempts==1


@pytest.mark.no_db
def test_monitoring_does_not_include_customer_payloads():
    event=scrub_event({"request":{"data":"password"},"user":{"email":"private"},"extra":{"secret":"value"},"message":"Error"},None)
    assert event=={"message":"Error"}


@pytest.mark.no_db
@pytest.mark.parametrize("path",["/privacy","/terms","/refunds","/shipping","/contact"])
def test_public_policies_render(client,path):
    response=client.get(path)
    assert response.status_code==200
    assert "support@revqr.tech" in response.text
    assert "RevQR" in response.text
