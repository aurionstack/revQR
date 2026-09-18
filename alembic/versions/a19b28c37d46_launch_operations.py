"""Payment safety and launch operations."""
from alembic import op
import sqlalchemy as sa

revision = "a19b28c37d46"
down_revision = "f4a0c2d9e1b7"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("entitlement_applied", "billing_review_required"):
        op.add_column("payments", sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE payments SET entitlement_applied = true WHERE status = 'paid'")
    op.add_column("payments", sa.Column("refunded_amount", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("payments", sa.Column("tracking_number", sa.String(100)))
    op.add_column("payments", sa.Column("tracking_url", sa.String(500)))
    op.add_column("payments", sa.Column("last_checked_at", sa.DateTime(timezone=True)))
    op.create_table("webhook_events", sa.Column("event_id",sa.String(128),primary_key=True), sa.Column("event_type",sa.String(80),nullable=False), sa.Column("status",sa.String(24),nullable=False,server_default="received"), sa.Column("received_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_table("usage_counters",sa.Column("key",sa.String(200),primary_key=True),sa.Column("count",sa.Integer(),nullable=False,server_default="0"),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_table("notifications",sa.Column("id",sa.UUID(),primary_key=True),sa.Column("dedupe_key",sa.String(200),nullable=False,unique=True),sa.Column("recipient",sa.String(255),nullable=False),sa.Column("subject",sa.String(255),nullable=False),sa.Column("message",sa.Text(),nullable=False),sa.Column("attempts",sa.Integer(),nullable=False,server_default="0"),sa.Column("sent_at",sa.DateTime(timezone=True)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_table("audit_logs",sa.Column("id",sa.UUID(),primary_key=True),sa.Column("actor_id",sa.UUID()),sa.Column("action",sa.String(255),nullable=False),sa.Column("target",sa.String(255)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))


def downgrade():
    for table in ("audit_logs","notifications","usage_counters","webhook_events"):
        op.drop_table(table)
    for column in ("last_checked_at","tracking_url","tracking_number","refunded_amount","billing_review_required","entitlement_applied"):
        op.drop_column("payments",column)
