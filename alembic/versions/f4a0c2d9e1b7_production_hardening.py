"""production hardening

Revision ID: f4a0c2d9e1b7
Revises: bcd4c96023fe
Create Date: 2026-09-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4a0c2d9e1b7"
down_revision: Union[str, Sequence[str], None] = "bcd4c96023fe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing accounts are considered verified so this rollout never locks
    # current customers out. All accounts created after this migration start
    # unverified through the new database default.
    op.add_column(
        "businesses",
        sa.Column("email_verified", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.execute("UPDATE businesses SET email_verified = true")
    op.alter_column("businesses", "email_verified", server_default=sa.false())
    op.add_column("businesses", sa.Column("email_otp_hash", sa.String(64), nullable=True))
    op.add_column("businesses", sa.Column("email_otp_purpose", sa.String(32), nullable=True))
    op.add_column("businesses", sa.Column("email_otp_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("businesses", sa.Column("email_otp_last_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("businesses", sa.Column("email_otp_attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("businesses", sa.Column("password_version", sa.Integer(), server_default="0", nullable=False))
    op.add_column("businesses", sa.Column("subscription_plan", sa.String(20), nullable=True))
    op.add_column("businesses", sa.Column("subscription_expires_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("payments", sa.Column("purpose", sa.String(32), server_default="subscription", nullable=False))
    op.add_column("payments", sa.Column("plan_code", sa.String(20), nullable=True))
    op.add_column("payments", sa.Column("quantity", sa.Integer(), server_default="1", nullable=False))
    op.add_column("payments", sa.Column("shipping_name", sa.String(255), nullable=True))
    op.add_column("payments", sa.Column("shipping_phone", sa.String(20), nullable=True))
    op.add_column("payments", sa.Column("shipping_address", sa.Text(), nullable=True))
    op.add_column("payments", sa.Column("shipping_postal_code", sa.String(12), nullable=True))
    op.add_column("payments", sa.Column("fulfillment_status", sa.String(24), nullable=True))
    op.execute("UPDATE payments SET plan_code = 'annual' WHERE purpose = 'subscription' AND plan_code IS NULL")

    op.create_table(
        "business_assets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(24), server_default="logo", nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id"),
    )
    op.create_index("ix_business_assets_business_id", "business_assets", ["business_id"])


def downgrade() -> None:
    op.drop_index("ix_business_assets_business_id", table_name="business_assets")
    op.drop_table("business_assets")

    for column in (
        "fulfillment_status",
        "shipping_postal_code",
        "shipping_address",
        "shipping_phone",
        "shipping_name",
        "quantity",
        "plan_code",
        "purpose",
    ):
        op.drop_column("payments", column)

    for column in (
        "subscription_expires_at",
        "subscription_plan",
        "password_version",
        "email_otp_attempts",
        "email_otp_last_sent_at",
        "email_otp_expires_at",
        "email_otp_purpose",
        "email_otp_hash",
        "email_verified",
    ):
        op.drop_column("businesses", column)
