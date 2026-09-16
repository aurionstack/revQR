import os
import asyncio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.main import app
from app.models import Base, Business
from app.database import get_db

TEST_DATABASE_URL = os.environ.get("DATABASE_URL_TEST", "postgresql+asyncpg://postgres:postgres@localhost:5432/qr_reviews_test")

@pytest.fixture(autouse=True)
def prevent_live_metadata_requests(monkeypatch):
    async def unavailable(*args):
        return '{"version":1,"description":null,"status":"unavailable"}'
    for module in ("app.routers.auth", "app.routers.dashboard", "app.routers.admin", "app.routers.review"):
        monkeypatch.setattr(module + ".import_business_context", unavailable)

@pytest.fixture(scope="function", autouse=True)
async def setup_test_db(request):
    if request.node.get_closest_marker("no_db"):
        yield
        return

    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest.fixture(scope="function")
def client():
    # We need to override get_db here
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    TestingSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async def override_get_db():
        async with TestingSessionLocal() as session:
            try:
                yield session
            finally:
                await session.close()
                
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
    app.dependency_overrides.clear()
    
@pytest.fixture(scope="function")
async def db_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    TestingSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with TestingSessionLocal() as session:
        yield session
    await engine.dispose()

@pytest.fixture
async def test_business(db_session: AsyncSession):
    """Create a dummy business in the test database."""
    from app.services.auth import get_password_hash
    import uuid
    unique_id = uuid.uuid4().hex[:8]
    biz = Business(
        name=f"Test Business {unique_id}",
        slug=f"test-business-{unique_id}",
        email=f"test_{unique_id}@example.com",
        password_hash=get_password_hash("password123"),
        is_active=True,
        email_verified=True,
    )
    db_session.add(biz)
    await db_session.commit()
    await db_session.refresh(biz)
    yield biz
    
    # Clean up (optional as tables are dropped at session end, 
    # but good for isolation if we don't truncate between tests)
    await db_session.delete(biz)
    await db_session.commit()
