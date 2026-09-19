"""Durable notifications and audit records. Never store passwords or API keys."""
import uuid
from datetime import datetime, timezone
from html import escape
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.models import Notification, AuditLog
from app.services.email import _send_email


async def queue_notification(db, key, recipient, subject, message):
    await db.execute(insert(Notification).values(id=uuid.uuid4(), dedupe_key=key, recipient=recipient,
        subject=subject[:255], message=message, attempts=0).on_conflict_do_nothing(index_elements=[Notification.dedupe_key]))


async def audit(db, actor_id, action, target=None):
    db.add(AuditLog(actor_id=actor_id, action=action[:255], target=str(target)[:255] if target else None))


async def send_queued_notification(db, dedupe_key):
    """Try one durable outbox message immediately; failed mail remains queued."""
    row = (await db.execute(select(Notification).where(
        Notification.dedupe_key == dedupe_key,
        Notification.sent_at.is_(None),
        Notification.attempts < 5,
    ).with_for_update())).scalar_one_or_none()
    if not row:
        await db.rollback()
        return False
    row.attempts += 1
    sent = await _send_email(row.recipient, row.subject,
        "<p>" + escape(row.message).replace("\n", "<br />") + "</p>", row.message)
    if sent:
        row.sent_at = datetime.now(timezone.utc)
    await db.commit()
    return sent
