import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ── Business Schemas ──────────────────────────────────────────────────────────


class BusinessCreate(BaseModel):
    """Schema for business registration."""
    name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    google_place_id: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)


class BusinessLogin(BaseModel):
    """Schema for business login."""
    email: EmailStr
    password: str


class BusinessUpdate(BaseModel):
    """Schema for updating business settings."""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    google_place_id: Optional[str] = Field(None, max_length=255)
    brand_color: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    phone: Optional[str] = Field(None, max_length=20)


class BusinessResponse(BaseModel):
    """Schema for returning business data (no password hash)."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    email: str
    google_place_id: Optional[str]
    logo_url: Optional[str]
    brand_color: str
    phone: Optional[str]
    is_active: bool
    created_at: datetime


# ── Review Flow Schemas ───────────────────────────────────────────────────────


class RatingSubmit(BaseModel):
    """Schema for submitting a star rating."""
    rating: int = Field(..., ge=1, le=5)


class ReviewGenerateRequest(BaseModel):
    """Schema for requesting AI review generation."""
    rating: int = Field(..., ge=1, le=5)
    customer_notes: str = Field(..., min_length=1, max_length=500)
    scan_id: Optional[uuid.UUID] = None


class ReviewResponse(BaseModel):
    """Schema for returning a generated review."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rating: int
    customer_notes: str
    generated_text: str
    final_text: Optional[str]
    copied: bool
    redirected: bool
    created_at: datetime


class ReviewTrackAction(BaseModel):
    """Schema for tracking copy/redirect actions."""
    review_id: uuid.UUID
    final_text: Optional[str] = None  # capture edited text on copy


# ── Feedback Schemas ──────────────────────────────────────────────────────────


class FeedbackCreate(BaseModel):
    """Schema for submitting optional private feedback."""
    message: str = Field(..., min_length=1, max_length=2000)
    rating: Optional[int] = Field(None, ge=1, le=5)
    scan_id: Optional[uuid.UUID] = None
    review_id: Optional[uuid.UUID] = None


class FeedbackResponse(BaseModel):
    """Schema for returning feedback data."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rating: Optional[int]
    message: str
    created_at: datetime


# ── Scan Schemas ──────────────────────────────────────────────────────────────


class ScanResponse(BaseModel):
    """Schema for returning scan data."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    scanned_at: datetime


# ── Dashboard / Stats Schemas ─────────────────────────────────────────────────


class DashboardStats(BaseModel):
    """Aggregated stats for the business dashboard."""
    total_scans: int = 0
    total_reviews: int = 0
    total_copied: int = 0
    total_redirected: int = 0
    total_feedback: int = 0
    average_rating: Optional[float] = None
    conversion_rate: Optional[float] = None  # reviews / scans


# ── Auth Token ────────────────────────────────────────────────────────────────


class TokenData(BaseModel):
    """JWT token payload."""
    business_id: uuid.UUID
    email: str
