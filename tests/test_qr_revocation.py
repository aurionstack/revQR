from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.models import AuditLog
from app.routers.admin import set_qr_access


@pytest.mark.asyncio
async def test_revoke_and_restore_preserves_subscription_and_blocks_customer_flow(db_session,test_business,client):
    test_business.has_paid=True
    await db_session.commit()
    admin=SimpleNamespace(id=test_business.id)
    await set_qr_access(test_business.id,True,True,admin,db_session)
    assert test_business.qr_revoked
    assert test_business.has_paid and test_business.has_active_subscription
    assert not test_business.has_qr_access
    assert client.get('/review/'+test_business.slug).status_code==403
    # Router-wide dependency blocks ongoing sessions as well as fresh scans.
    assert client.post('/review/'+test_business.slug+'/rate',data={'rating':5,'scan_id':'invalid'}).status_code==403
    await set_qr_access(test_business.id,False,True,admin,db_session)
    assert not test_business.qr_revoked and test_business.has_qr_access
    assert client.get('/review/'+test_business.slug).status_code==200
    logs=(await db_session.execute(select(AuditLog.action))).scalars().all()
    assert 'qr.revoked' in logs and 'qr.restored' in logs


@pytest.mark.asyncio
async def test_confirmation_required_and_admin_target_protected(db_session,test_business):
    admin=SimpleNamespace(id=test_business.id)
    with pytest.raises(HTTPException) as error:
        await set_qr_access(test_business.id,True,False,admin,db_session)
    assert error.value.status_code==400
    test_business.is_admin=True
    await db_session.commit()
    with pytest.raises(HTTPException) as error:
        await set_qr_access(test_business.id,True,True,admin,db_session)
    assert error.value.status_code==400 and not test_business.qr_revoked
