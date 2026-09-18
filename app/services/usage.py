"""Atomic PostgreSQL counters shared by all workers. Reservations fail closed."""
from datetime import datetime, timezone
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import func
from app.models import UsageCounter
from app.config import settings


async def _reserve(db, key, limit, amount=1):
    if limit <= 0 or amount > limit:
        return False
    statement = insert(UsageCounter).values(key=key, count=amount).on_conflict_do_update(
        index_elements=[UsageCounter.key],
        set_={"count": UsageCounter.count + amount, "updated_at": func.now()},
        where=UsageCounter.count + amount <= limit,
    ).returning(UsageCounter.count)
    return (await db.execute(statement)).scalar_one_or_none() is not None


async def reserve_business_generation(db, business_id, scan_id=None):
    now = datetime.now(timezone.utc)
    limits = [(f"business:{business_id}:month:{now:%Y-%m}",settings.AI_MONTHLY_LIMIT),
              (f"business:{business_id}:day:{now:%Y-%m-%d}",settings.AI_DAILY_BUSINESS_LIMIT)]
    if scan_id:
        limits.append((f"scan:{scan_id}", settings.AI_SCAN_LIMIT))
    # Nested transaction rolls back all reservations when any bucket is full.
    async with db.begin_nested() as transaction:
        for key, limit in sorted(limits):
            if not await _reserve(db, key, limit):
                await transaction.rollback()
                return False
    await db.commit()  # release counter locks before external AI calls
    return True


async def reserve_provider_budget(contents, config):
    from app.database import async_session_factory
    now = datetime.now(timezone.utc)
    # Conservative token reservation, not a claimed dollar-cost measurement.
    # Counts every provider attempt, including rotation retries.
    tokens = len(str(contents)) + len(str(getattr(config,"system_instruction", "") or "")) + int(getattr(config,"max_output_tokens",3500) or 3500)
    async with async_session_factory() as db:
        if not await _reserve(db, f"global:calls:{now:%Y-%m-%d}",settings.AI_GLOBAL_DAILY_CALL_LIMIT):
            await db.rollback()
            return False
        if not await _reserve(db, f"global:tokens:{now:%Y-%m-%d}",settings.AI_GLOBAL_DAILY_TOKEN_BUDGET,tokens):
            await db.rollback()
            return False
        await db.commit()
        return True
