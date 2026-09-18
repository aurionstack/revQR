"""Run hourly: python -m app.maintenance. No charges/refunds are initiated."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from html import escape
from sqlalchemy import select, or_, update
from app.database import async_session_factory, engine
from app.models import Business, Payment, Notification
from app.services.operations import queue_notification
from app.services.razorpay import reconcile_order
from app.services.email import _send_email
from app.config import settings

logger=logging.getLogger(__name__)


async def enqueue_renewals(db):
    now=datetime.now(timezone.utc)
    businesses=(await db.execute(select(Business).where(Business.is_admin==False,Business.has_paid==True,
        Business.subscription_expires_at<=now+timedelta(days=30),Business.subscription_expires_at>=now-timedelta(days=7)))).scalars().all()
    for business in businesses:
        days=(business.subscription_expires_at-now).total_seconds()/86400
        window="expired" if days<=0 else "1" if days<=1 else "7" if days<=7 else "30"
        await queue_notification(db,f"expiry:{business.id}:{business.subscription_expires_at.isoformat()}:{window}",business.email,
            "RevQR subscription reminder",f"Your subscription {'expired' if days<=0 else 'expires'} on {business.subscription_expires_at:%d %b %Y}. Renewal is manual; we will not automatically charge you. Review your account at {settings.APP_URL}/dashboard/billing.")
    await db.commit()


async def deliver_notifications(db):
    # A durable outbox prevents lost emails. SMTP cannot guarantee exactly-once
    # delivery across a crash after send; receipts may occasionally be repeated.
    rows=(await db.execute(select(Notification).where(Notification.sent_at.is_(None),Notification.attempts<5)
        .order_by(Notification.created_at).limit(50).with_for_update(skip_locked=True))).scalars().all()
    for row in rows:
        row.attempts+=1
        sent=await _send_email(row.recipient,row.subject,"<p>"+escape(row.message).replace("\n","<br />")+"</p>",row.message)
        if sent:
            row.sent_at=datetime.now(timezone.utc)
    await db.commit()


async def run():
    async with engine.connect() as coordinator, async_session_factory() as db:
        # Prevent overlapping hourly jobs across dynos without a paid add-on.
        from sqlalchemy import text
        lock=(await coordinator.execute(text("SELECT pg_try_advisory_lock(18092026)"))).scalar()
        if not lock:
            return
        try:
            now=datetime.now(timezone.utc)
            if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
                ids=(await db.execute(select(Payment.razorpay_order_id).where(
                    Payment.created_at>now-timedelta(days=30),
                    Payment.status.in_(["created","failed","paid","partially_refunded"]),
                    or_(Payment.last_checked_at.is_(None),Payment.last_checked_at<now-timedelta(hours=1)))
                    .order_by(Payment.last_checked_at.asc().nullsfirst(),Payment.created_at).limit(60))).scalars().all()
                await db.commit()
                for order_id in ids:
                    try:
                        await reconcile_order(order_id,db)
                    except Exception as exc:
                        await db.rollback()
                        await db.execute(update(Payment).where(Payment.razorpay_order_id == order_id)
                            .values(last_checked_at=datetime.now(timezone.utc)))
                        await db.commit()
                        logger.error("Payment reconciliation failed (%s)",type(exc).__name__)
            await enqueue_renewals(db)
            await deliver_notifications(db)
        finally:
            await coordinator.execute(text("SELECT pg_advisory_unlock(18092026)"))
            await coordinator.commit()
    await engine.dispose()


if __name__=="__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
