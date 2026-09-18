"""Durable notifications and audit records. Never store passwords or API keys."""
import uuid
from sqlalchemy.dialects.postgresql import insert
from app.models import Notification, AuditLog


async def queue_notification(db, key, recipient, subject, message):
    await db.execute(insert(Notification).values(id=uuid.uuid4(), dedupe_key=key, recipient=recipient,
        subject=subject[:255], message=message, attempts=0).on_conflict_do_nothing(index_elements=[Notification.dedupe_key]))


async def audit(db, actor_id, action, target=None):
    db.add(AuditLog(actor_id=actor_id, action=action[:255], target=str(target)[:255] if target else None))
