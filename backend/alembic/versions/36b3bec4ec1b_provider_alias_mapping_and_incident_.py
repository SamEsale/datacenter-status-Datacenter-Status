"""provider alias mapping and incident audit

Revision ID: 36b3bec4ec1b
Revises: 30d77bbf26e2
Create Date: 2026-02-19

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "36b3bec4ec1b"
down_revision = "30d77bbf26e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1) provider_aliases: allowlisted provider matching rules
    # -------------------------------------------------------------------------
    op.create_table(
        "provider_aliases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider_id", sa.Integer(), sa.ForeignKey("providers.id"), nullable=False),
        sa.Column("match_type", sa.String(length=30), nullable=False),
        sa.Column("match_value", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("match_type", "match_value", name="uq_provider_aliases_match_type_value"),
    )

    op.create_index(
        "ix_provider_aliases_provider_id",
        "provider_aliases",
        ["provider_id"],
        unique=False,
    )

    op.create_index(
        "ix_provider_aliases_match_type_value_active",
        "provider_aliases",
        ["match_type", "match_value", "is_active"],
        unique=False,
    )

    # -------------------------------------------------------------------------
    # 2) Incident classification audit fields (expand-only)
    # -------------------------------------------------------------------------
    op.add_column("incidents", sa.Column("provider_match_type", sa.String(length=30), nullable=True))
    op.add_column("incidents", sa.Column("provider_match_value", sa.Text(), nullable=True))
    op.add_column("incidents", sa.Column("provider_confidence", sa.Integer(), nullable=True))

    # Useful operational metadata for later repair & traceability (optional but recommended)
    op.add_column("incidents", sa.Column("from_address", sa.Text(), nullable=True))
    op.add_column("incidents", sa.Column("subject", sa.Text(), nullable=True))

    # Index audit fields for troubleshooting / targeted repairs
    op.create_index("ix_incidents_provider_match_type", "incidents", ["provider_match_type"], unique=False)
    op.create_index("ix_incidents_provider_confidence", "incidents", ["provider_confidence"], unique=False)


def downgrade() -> None:
    # Reverse order (drop indexes -> columns -> table)
    op.drop_index("ix_incidents_provider_confidence", table_name="incidents")
    op.drop_index("ix_incidents_provider_match_type", table_name="incidents")

    op.drop_column("incidents", "subject")
    op.drop_column("incidents", "from_address")

    op.drop_column("incidents", "provider_confidence")
    op.drop_column("incidents", "provider_match_value")
    op.drop_column("incidents", "provider_match_type")

    op.drop_index("ix_provider_aliases_match_type_value_active", table_name="provider_aliases")
    op.drop_index("ix_provider_aliases_provider_id", table_name="provider_aliases")
    op.drop_table("provider_aliases")
