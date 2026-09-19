import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, Integer, Text, ForeignKey, DateTime, LargeBinary, func
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
    qr_revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    has_paid: Mapped[bool] = mapped_column(Boolean, default=False)  # unlocks QR generation
    totp_secret: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_2fa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    email_otp_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email_otp_purpose: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email_otp_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_otp_last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_otp_attempts: Mapped[int] = mapped_column(Integer, default=0)
    password_version: Mapped[int] = mapped_column(Integer, default=0)
    subscription_plan: Mapped[str | None] = mapped_column(String(20), nullable=True)
    subscription_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    custom_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    scraped_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    # Child tables already use ON DELETE CASCADE. passive_deletes="all" stops
    # SQLAlchemy from trying to set their non-nullable business_id to NULL when
    # a business is removed.
    scans: Mapped[list["Scan"]] = relationship(
        back_populates="business", lazy="noload", passive_deletes="all"
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="business", lazy="noload", passive_deletes="all"
    )
    feedback_items: Mapped[list["Feedback"]] = relationship(
        back_populates="business", lazy="noload", passive_deletes="all"
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="business", lazy="noload", passive_deletes="all"
    )
    assets: Mapped[list["BusinessAsset"]] = relationship(
        back_populates="business", lazy="noload", passive_deletes="all"
    )

    @property
    def has_qr_access(self) -> bool:
        return bool(self.is_active is not False and not self.qr_revoked and self.has_active_subscription)

    @property
    def has_active_subscription(self) -> bool:
        """Return whether paid features are currently available."""
        if not self.has_paid:
            return False
        # Existing lifetime purchases remain grandfathered.
        if self.subscription_expires_at is None:
            return True
        expires_at = self.subscription_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc)

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
    purpose: Mapped[str] = mapped_column(String(32), default="subscription")
    plan_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    shipping_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    shipping_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    shipping_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    shipping_postal_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    fulfillment_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="created")  # created | paid | failed
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    entitlement_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    billing_review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    refunded_amount: Mapped[int] = mapped_column(Integer, default=0)
    tracking_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tracking_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    business: Mapped["Business"] = relationship(back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment {self.razorpay_order_id} ({self.status}) for {self.business_id}>"


class BusinessAsset(Base):
    """Durable business assets stored in PostgreSQL instead of ephemeral disk."""
    __tablename__ = "business_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(24), default="logo")
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    business: Mapped["Business"] = relationship(back_populates="assets")


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="received")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageCounter(Base):
    __tablename__ = "usage_counters"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
