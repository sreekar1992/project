"""Initial ECG health-platform relational schema.

The metadata-driven initial revision is intentionally portable across the
supported SQLite development database and PostgreSQL deployment database.
Subsequent migrations must use explicit Alembic operations.
"""
from alembic import op

from ecg_cvd.clinical.db import Base
import ecg_cvd.clinical.models  # noqa: F401 - registers metadata


revision = "20260924_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
