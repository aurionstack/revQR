import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone
from unittest.mock import Mock
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.config import settings
from app.models import Payment, Business, WebhookEvent, Notification
from app.services import razorpay as service


@pytest.fixture
async def purchase(db_session,test_business):
    payment=Payment(business_id=test_business.id,razorpay_order_id="order_launch_test",amount=99900,
        currency="INR",purpose="subscription",plan_code="annual",status="created")
    db_session.add(payment)
    await db_session.commit()
    return payment


@pytest.fixture
def provider(monkeypatch):
    entity={"id":"pay_launch_test","order_id":"order_launch_test","amount":99900,"currency":"INR","status":"captured","amount_refunded":0}
    monkeypatch.setattr(settings,"RAZORPAY_KEY_SECRET","callback-test-secret")
    monkeypatch.setattr(settings,"RAZORPAY_WEBHOOK_SECRET","webhook-test-secret")
    fetch=Mock(side_effect=lambda pid:dict(entity))
    monkeypatch.setattr(service.client.payment,"fetch",fetch)
    monkeypatch.setattr(service.client.order,"fetch",Mock(return_value={"id":entity["order_id"],"amount":99900,"currency":"INR","status":"paid"}))
    monkeypatch.setattr(service.client.order,"payments",Mock(side_effect=lambda oid:{"items":[dict(entity)]}))
    return entity,fetch


def callback_signature():
    return hmac.new(settings.RAZORPAY_KEY_SECRET.encode(),b"order_launch_test|pay_launch_test",hashlib.sha256).hexdigest()


def webhook(event="payment.captured",kind="payment",entity=None):
    raw=json.dumps({"event":event,"payload":{kind:{"entity":entity or {"id":"pay_launch_test"}}}}).encode()
    return raw,hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(),raw,hashlib.sha256).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("change",[{"status":"authorized"},{"amount":1},{"currency":"USD"},{"order_id":"other-order"},{"amount_refunded":99900}])
async def test_callback_never_activates_unconfirmed_purchase(change,provider,purchase,db_session,test_business):
    business_id = test_business.id
    provider[0].update(change)
    assert not await service.verify_payment(purchase.razorpay_order_id,"pay_launch_test",callback_signature(),db_session,test_business.id)
    await db_session.rollback()
    assert not (await db_session.get(Business,business_id)).has_paid


@pytest.mark.asyncio
async def test_callback_and_webhook_race_extend_only_once(provider,purchase,db_session,test_business):
    business_id=test_business.id
    factory=async_sessionmaker(db_session.bind,expire_on_commit=False)
    raw,signature=webhook()
    async def callback():
        async with factory() as db:
            return await service.verify_payment("order_launch_test","pay_launch_test",callback_signature(),db,business_id)
    async def event():
        async with factory() as db:
            return await service.handle_webhook(raw,signature,db,"event-concurrent")
    assert all(await asyncio.gather(callback(),event()))
    db_session.expire_all()
    business=await db_session.get(Business,business_id)
    days=(business.subscription_expires_at-datetime.now(timezone.utc)).days
    assert 364 <= days <= 366
    assert (await db_session.execute(select(func.count(Notification.id)))).scalar()==1


@pytest.mark.asyncio
async def test_duplicate_event_and_delayed_capture_do_not_reactivate(provider,purchase,db_session,test_business):
    raw,signature=webhook()
    assert await service.handle_webhook(raw,signature,db_session,"evt-capture")
    expiry=(await db_session.get(Business,test_business.id)).subscription_expires_at
    assert await service.handle_webhook(raw,signature,db_session,"evt-capture")
    assert provider[1].call_count==1
    provider[0].update(status="refunded",amount_refunded=99900)
    refund,sig=webhook("refund.processed","refund",{"payment_id":"pay_launch_test"})
    assert await service.handle_webhook(refund,sig,db_session,"evt-refund")
    assert await service.handle_webhook(raw,signature,db_session,"evt-delayed-capture")
    await db_session.refresh(purchase)
    assert purchase.status=="refunded" and purchase.billing_review_required
    assert (await db_session.get(Business,test_business.id)).subscription_expires_at==expiry


@pytest.mark.asyncio
async def test_missing_order_event_is_retryable_and_not_deduplicated(provider,db_session):
    raw,signature=webhook()
    with pytest.raises(service.RetryableWebhookError):
        await service.handle_webhook(raw,signature,db_session,"evt-missing")
    assert await db_session.get(WebhookEvent,"evt-missing") is None


@pytest.mark.asyncio
async def test_bad_webhook_signature_never_contacts_provider(provider,purchase,db_session):
    raw,_=webhook()
    assert not await service.handle_webhook(raw,"bad-signature",db_session)
    assert provider[1].call_count==0


@pytest.mark.asyncio
async def test_reconciliation_recovers_missing_browser_callback(provider,purchase,db_session,test_business):
    assert await service.reconcile_order("order_launch_test",db_session,test_business.id)
    await db_session.refresh(purchase)
    assert purchase.status=="paid" and purchase.entitlement_applied


@pytest.mark.asyncio
async def test_live_one_rupee_checkout_is_restricted_to_verified_allowlisted_admin(monkeypatch,db_session,test_business):
    monkeypatch.setattr(settings,"RAZORPAY_KEY_ID","rzp_live_test")
    monkeypatch.setattr(settings,"RAZORPAY_KEY_SECRET","secret-test")
    monkeypatch.setattr(settings,"RAZORPAY_WEBHOOK_SECRET","webhook-test")
    monkeypatch.setattr(settings,"PUBLIC_CHECKOUT_ENABLED",False)
    monkeypatch.setattr(settings,"POLICIES_APPROVED",False)
    monkeypatch.setattr(settings,"LIVE_PAYMENT_TEST_EMAIL",test_business.email)
    create=Mock(side_effect=lambda data:{"id":"order_one_rupee","amount":data['amount'],"currency":"INR"})
    monkeypatch.setattr(service.client.order,"create",create)
    with pytest.raises(service.PaymentConfigurationError):
        await service.create_order(test_business.id,db_session,plan_code="annual")
    test_business.is_admin=True
    await db_session.commit()
    result=await service.create_order(test_business.id,db_session,plan_code="annual")
    assert result['amount']==100
    with pytest.raises(service.PaymentConfigurationError):
        await service.create_order(test_business.id,db_session,plan_code="two_year")
    monkeypatch.setattr(settings,"LIVE_PAYMENT_TEST_EMAIL","another@example.com")
    with pytest.raises(service.PaymentConfigurationError):
        await service.create_order(test_business.id,db_session,plan_code="annual")
    assert create.call_count==1
