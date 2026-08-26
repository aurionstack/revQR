import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, Integer, Text, ForeignKey, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


class Business(Base):
    """A business that uses QR Reviews to collect Google reviews."""
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    google_place_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    brand_color: Mapped[str] = mapped_column(String(7), default="#6366f1")
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)  # admin bypasses payment
    has_paid: Mapped[bool] = mapped_column(Boolean, default=False)  # unlocks QR generation
    custom_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    scraped_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    scans: Mapped[list["Scan"]] = relationship(back_populates="business", lazy="selectin")
    reviews: Mapped[list["Review"]] = relationship(back_populates="business", lazy="selectin")
    feedback_items: Mapped[list["Feedback"]] = relationship(back_populates="business", lazy="selectin")
    payments: Mapped[list["Payment"]] = relationship(back_populates="business", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Business {self.name} ({self.slug})>"


class Scan(Base):
    """Records every QR code scan, even if the customer doesn't complete a review."""
    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g., 'table_1', 'whatsapp', 'rahul'
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    business: Mapped["Business"] = relationship(back_populates="scans")
    review: Mapped["Review | None"] = relationship(back_populates="scan", uselist=False)
    feedback: Mapped["Feedback | None"] = relationship(back_populates="scan", uselist=False)

    def __repr__(self) -> str:
        return f"<Scan {self.id} for business {self.business_id}>"


class Review(Base):
    """
    An AI-generated review for any star rating (1-5).
    Every customer goes through the same path — no review gating.
    """
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="SET NULL"), nullable=True
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-5, any rating
    customer_notes: Mapped[str] = mapped_column(Text, nullable=False)
    generated_text: Mapped[str] = mapped_column(Text, nullable=False)
    final_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # after customer edits
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    copied: Mapped[bool] = mapped_column(Boolean, default=False)
    redirected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    business: Mapped["Business"] = relationship(back_populates="reviews")
    scan: Mapped["Scan | None"] = relationship(back_populates="review")
    feedback: Mapped["Feedback | None"] = relationship(back_populates="review", uselist=False)

    def __repr__(self) -> str:
        return f"<Review {self.id} ({self.rating}★) for {self.business_id}>"


class Feedback(Base):
    """
    Optional private note a customer can send to the business.
    Available to every customer regardless of rating — never a substitute
    for the Google review step (no review gating).
    """
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="SET NULL"), nullable=True
    )
    review_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="SET NULL"), nullable=True
    )
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5, optional
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    business: Mapped["Business"] = relationship(back_populates="feedback_items")
    scan: Mapped["Scan | None"] = relationship(back_populates="feedback")
    review: Mapped["Review | None"] = relationship(back_populates="feedback")

    def __repr__(self) -> str:
        return f"<Feedback {self.id} for {self.business_id}>"


class Payment(Base):
    """Tracks Razorpay payment orders for QR code generation."""
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    razorpay_order_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    razorpay_signature: Mapped[str | None] = mapped_column(String(500), nullable=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # in paise
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(20), default="created")  # created | paid | failed
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    business: Mapped["Business"] = relationship(back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment {self.razorpay_order_id} ({self.status}) for {self.business_id}>"
