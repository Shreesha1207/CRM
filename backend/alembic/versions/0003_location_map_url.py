"""google maps link for locations

A location may carry its own Google Maps link (a shared place link, say);
without one, the app links to a Maps search for its name and address.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-27 09:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('locations', sa.Column('map_url', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('locations', 'map_url')
