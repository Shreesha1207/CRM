"""user status changed at

When an account last changed status, so an admin may delete a customer
account only once it has been inactive for long enough.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-22 09:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('status_changed_at', sa.DateTime(timezone=True), nullable=True))
    # The best guess for accounts that are already inactive: their last change.
    op.execute("UPDATE users SET status_changed_at = updated_at WHERE status <> 'ACTIVE'")


def downgrade() -> None:
    op.drop_column('users', 'status_changed_at')
