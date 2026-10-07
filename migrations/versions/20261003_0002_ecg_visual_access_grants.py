"""Add doctor-approved, visual-only ECG access grants.

The passcode itself is never persisted.  ``secret_envelope`` holds only a
password-derived AES-256-GCM envelope for a random per-grant secret, and the
browser token field holds only a JTI digest for server-side revocation.
"""
from alembic import op
import sqlalchemy as sa


revision = "20261003_0002"
down_revision = "20260924_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The original revision deliberately uses ``Base.metadata.create_all`` so
    # a fresh checkout imports the current metadata while applying revision
    # 0001.  Existing deployed databases at revision 0001 do not have this
    # table.  This guard keeps both upgrade paths safe without rewriting the
    # released initial revision.
    if "ecg_visual_access_grants" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "ecg_visual_access_grants",
        sa.Column("visual_access_grant_uuid", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ecg_uuid", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("requester_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("approved_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="PENDING"),
        sa.Column("request_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("passcode_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("secret_envelope", sa.LargeBinary(), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("unlock_token_jti_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("failed_attempts >= 0", name="ck_visual_access_failed_attempts_nonnegative"),
        sa.CheckConstraint("max_attempts > 0", name="ck_visual_access_max_attempts_positive"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.user_id"]),
        sa.ForeignKeyConstraint(["ecg_uuid"], ["ecg_records.ecg_uuid"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.organization_id"]),
        sa.ForeignKeyConstraint(["requester_id"], ["users.user_id"]),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("visual_access_grant_uuid"),
    )
    op.create_index("ix_ecg_visual_access_grants_ecg_uuid", "ecg_visual_access_grants", ["ecg_uuid"])
    op.create_index("ix_ecg_visual_access_grants_organization_id", "ecg_visual_access_grants", ["organization_id"])
    op.create_index("ix_ecg_visual_access_grants_requester_id", "ecg_visual_access_grants", ["requester_id"])
    op.create_index("ix_ecg_visual_access_grants_approved_by", "ecg_visual_access_grants", ["approved_by"])
    op.create_index("ix_ecg_visual_access_grants_revoked_by", "ecg_visual_access_grants", ["revoked_by"])
    op.create_index("ix_ecg_visual_access_grants_status", "ecg_visual_access_grants", ["status"])
    op.create_index("ix_visual_access_org_status", "ecg_visual_access_grants", ["organization_id", "status", "created_at"])
    op.create_index("ix_visual_access_requester_ecg", "ecg_visual_access_grants", ["requester_id", "ecg_uuid", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_visual_access_requester_ecg", table_name="ecg_visual_access_grants")
    op.drop_index("ix_visual_access_org_status", table_name="ecg_visual_access_grants")
    op.drop_index("ix_ecg_visual_access_grants_status", table_name="ecg_visual_access_grants")
    op.drop_index("ix_ecg_visual_access_grants_revoked_by", table_name="ecg_visual_access_grants")
    op.drop_index("ix_ecg_visual_access_grants_approved_by", table_name="ecg_visual_access_grants")
    op.drop_index("ix_ecg_visual_access_grants_requester_id", table_name="ecg_visual_access_grants")
    op.drop_index("ix_ecg_visual_access_grants_organization_id", table_name="ecg_visual_access_grants")
    op.drop_index("ix_ecg_visual_access_grants_ecg_uuid", table_name="ecg_visual_access_grants")
    op.drop_table("ecg_visual_access_grants")
