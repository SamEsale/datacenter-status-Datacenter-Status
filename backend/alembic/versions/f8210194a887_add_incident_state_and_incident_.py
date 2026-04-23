"""add incident state and incident_locations

Revision ID: f8210194a887
Revises: 8e398b539f71
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa


# IMPORTANT:
# Replace these with the values from the file Alembic created
revision = "f8210194a887"
down_revision = "8e398b539f71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Add incident state (safe default)
    op.add_column(
        "incidents",
        sa.Column("state", sa.String(length=20), nullable=False, server_default="active"),
    )
    op.create_index("ix_incidents_state", "incidents", ["state"])

    # Remove server_default after backfilling/creation so app owns future writes
    op.alter_column("incidents", "state", server_default=None)

    # 2) New normalized locations table
    op.create_table(
        "incident_locations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("postal_code", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=False, server_default="Sweden"),
        sa.Column("confidence", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    # 3) Backfill from incidents.affected_postal_codes CSV into incident_locations
    # We keep this deliberately simple/portable: split by ',' and trim spaces.
    # This is safe even if affected_postal_codes is NULL/empty.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, affected_postal_codes FROM incidents")).fetchall()

    inserts = []
    for inc_id, csv in rows:
        if not csv:
            continue
        parts = [p.strip() for p in str(csv).split(",") if p and p.strip()]
        # de-dup per incident
        seen = set()
        for code in parts:
            if code in seen:
                continue
            seen.add(code)
            inserts.append({"incident_id": inc_id, "postal_code": code, "country": "Sweden", "source": "legacy_csv"})

    if inserts:
        conn.execute(
            sa.text(
                """
                INSERT INTO incident_locations (incident_id, postal_code, country, source, created_at_utc)
                VALUES (:incident_id, :postal_code, :country, :source, CURRENT_TIMESTAMP)
                """
            ),
            inserts,
        )

    # Ensure country default is removed from server-side if you prefer app-layer ownership.
    # Leaving it is also fine. We'll leave it as-is for now (safe).


def downgrade() -> None:
    op.drop_table("incident_locations")

    op.drop_index("ix_incidents_state", table_name="incidents")
    op.drop_column("incidents", "state")
