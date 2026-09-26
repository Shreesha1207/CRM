"""flexible booking length, hourly rates

Services may let customers choose the booking length (multiples of the
service duration up to max_duration_minutes), so a price per session no
longer works: prices become hourly rates. Existing prices are converted so
that every service still costs the same for its default length.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26 13:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('services', sa.Column('max_duration_minutes', sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f('ck_services_max_duration_valid'),
        'services',
        'max_duration_minutes IS NULL OR max_duration_minutes >= duration_minutes',
    )
    # Link overrides first: they are converted with their own duration when set.
    op.execute(
        """
        UPDATE resource_services AS rs
           SET custom_price = round(rs.custom_price * 60 / coalesce(rs.custom_duration, s.duration_minutes), 2)
          FROM services AS s
         WHERE s.id = rs.service_id AND rs.custom_price IS NOT NULL
        """
    )
    op.execute("UPDATE services SET price = round(price * 60 / duration_minutes, 2) WHERE price IS NOT NULL")


def downgrade() -> None:
    op.execute("UPDATE services SET price = round(price * duration_minutes / 60, 2) WHERE price IS NOT NULL")
    op.execute(
        """
        UPDATE resource_services AS rs
           SET custom_price = round(rs.custom_price * coalesce(rs.custom_duration, s.duration_minutes) / 60, 2)
          FROM services AS s
         WHERE s.id = rs.service_id AND rs.custom_price IS NOT NULL
        """
    )
    op.drop_constraint(op.f('ck_services_max_duration_valid'), 'services', type_='check')
    op.drop_column('services', 'max_duration_minutes')
