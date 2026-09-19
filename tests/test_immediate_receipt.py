import pytest
from sqlalchemy import select
from app.models import Notification
from app.services.operations import queue_notification, send_queued_notification


@pytest.mark.asyncio
async def test_immediate_receipt_delivery_marks_durable_message(db_session, monkeypatch):
    async def sent(*args):
        return True
    monkeypatch.setattr("app.services.operations._send_email", sent)
    await queue_notification(db_session,"receipt:test-immediate","owner@example.com","Receipt","Paid")
    await db_session.commit()
    assert await send_queued_notification(db_session,"receipt:test-immediate")
    row=(await db_session.execute(select(Notification).where(Notification.dedupe_key=="receipt:test-immediate"))).scalar_one()
    assert row.sent_at is not None and row.attempts==1
    assert not await send_queued_notification(db_session,"receipt:test-immediate")


@pytest.mark.asyncio
async def test_failed_immediate_receipt_stays_queued(db_session, monkeypatch):
    async def failed(*args):
        return False
    monkeypatch.setattr("app.services.operations._send_email", failed)
    await queue_notification(db_session,"receipt:test-retry","owner@example.com","Receipt","Paid")
    await db_session.commit()
    assert not await send_queued_notification(db_session,"receipt:test-retry")
    row=(await db_session.execute(select(Notification).where(Notification.dedupe_key=="receipt:test-retry"))).scalar_one()
    assert row.sent_at is None and row.attempts==1
