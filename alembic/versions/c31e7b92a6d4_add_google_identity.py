"""Add Google OpenID Connect identity to business accounts."""

from alembic import op
import sqlalchemy as sa

revision = "c31e7b92a6d4"
down_revision = "b28c39d40e57"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("businesses", sa.Column("google_subject", sa.String(255), nullable=True))
    op.create_unique_constraint("uq_businesses_google_subject", "businesses", ["google_subject"])


def downgrade():
    op.drop_constraint("uq_businesses_google_subject", "businesses", type_="unique")
    op.drop_column("businesses", "google_subject")
