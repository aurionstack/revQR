import asyncio
import uuid
from sqlalchemy.future import select
from app.database import async_session_factory
from app.models import Business
from app.services.auth import get_password_hash

async def create_or_update_admin(name="Admin User", email="admin@qrreviews.app", password="AdminPassword123!", slug="admin"):
    async with async_session_factory() as session:
        # Check if user already exists with this email or slug
        res = await session.execute(select(Business).filter((Business.email == email) | (Business.slug == slug)))
        business = res.scalars().first()

        if business:
            print(f"Existing account found for {business.email}. Updating to admin...")
            business.name = name
            business.slug = slug
            business.email = email
            business.password_hash = get_password_hash(password)
            business.is_admin = True
            business.has_paid = True
            business.is_active = True
        else:
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
        print(f"Password: {password}")
        print(f"Slug: {slug}")
        print(f"Is Admin: {business.is_admin}")
        print(f"Has Paid: {business.has_paid}")

if __name__ == "__main__":
    asyncio.run(create_or_update_admin())
