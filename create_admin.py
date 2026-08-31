import asyncio
import uuid
from sqlalchemy.future import select
from app.config import settings
from app.database import async_session_factory
from app.models import Business
from app.services.auth import get_password_hash

async def create_or_update_admin(
    name: str | None = None,
    email: str | None = None,
    password: str | None = None,
    slug: str | None = None,
):
    name = name or settings.ADMIN_NAME
    email = email or settings.ADMIN_EMAIL
    password = password if password is not None else settings.ADMIN_PASSWORD
    slug = slug or settings.ADMIN_SLUG

    async with async_session_factory() as session:
        # Check if user already exists with this email or slug
        res = await session.execute(select(Business).filter((Business.email == email) | (Business.slug == slug)))
        business = res.scalars().first()

        if business:
            print(f"Existing account found for {business.email}. Updating to admin...")
            business.name = name
            business.slug = slug
            business.email = email
            # Never reset an existing admin password merely because the app
            # restarted. An explicit ADMIN_PASSWORD is required to change it.
            if password:
                business.password_hash = get_password_hash(password)
            business.is_admin = True
            business.has_paid = True
            business.is_active = True
        else:
            if not password:
                print("[Warning] Admin account does not exist and ADMIN_PASSWORD is not set.")
                return None
            print(f"Creating new admin account for {email}...")
            business = Business(
                id=uuid.uuid4(),
                name=name,
                slug=slug,
                email=email,
                password_hash=get_password_hash(password),
                brand_color="#6366f1",
                is_active=True,
                is_admin=True,
                has_paid=True
            )
            session.add(business)

        await session.commit()
        await session.refresh(business)
        print("SUCCESS")
        print(f"Email: {email}")
        print(f"Slug: {slug}")
        print(f"Is Admin: {business.is_admin}")
        print(f"Has Paid: {business.has_paid}")
        return business

if __name__ == "__main__":
    asyncio.run(create_or_update_admin())
