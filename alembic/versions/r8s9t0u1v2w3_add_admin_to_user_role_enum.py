"""add admin value to user_role_enum

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-06-30 00:00:00.000000

Seed the first admin manually after this migration runs (ALTER TYPE ... ADD VALUE
cannot create users). Run against the application database, replacing the placeholder:

    UPDATE users SET role = 'admin' WHERE email = 'admin@example.com';

e.g. via psql or `alembic`-adjacent tooling once the deploy is live.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "r8s9t0u1v2w3"
down_revision: str | None = "q7r8s9t0u1v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres forbids ALTER TYPE ... ADD VALUE inside a transaction block,
    # so run it in an autocommit block. IF NOT EXISTS keeps it idempotent.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'admin'")


def downgrade() -> None:
    # No-op: Postgres cannot drop a value from an existing enum type without
    # recreating the type and rewriting every dependent column, which is unsafe
    # to automate here. The 'admin' value is left in place.
    pass
