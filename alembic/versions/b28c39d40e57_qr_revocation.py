"""Independent administrative QR revocation."""
from alembic import op
import sqlalchemy as sa
revision = "b28c39d40e57"
down_revision = "a19b28c37d46"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("businesses", sa.Column("qr_revoked", sa.Boolean(), nullable=False, server_default=sa.false()))

def downgrade():
    op.drop_column("businesses", "qr_revoked")
