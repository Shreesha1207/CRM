"""booking invitations

An admin can book for an e-mail address that has no account yet. The requested
booking waits in this table until the invitee registers and accepts it.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-15 09:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'booking_invitations',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('token_hash', sa.String(length=128), nullable=False),
        sa.Column('invited_by_id', sa.UUID(), nullable=False),
        sa.Column('request', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'ACCEPTED', 'DECLINED', 'FAILED', name='invitationstatus', native_enum=False, length=32), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('booking_id', sa.UUID(), nullable=True),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], name=op.f('fk_booking_invitations_booking_id_bookings'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['invited_by_id'], ['users.id'], name=op.f('fk_booking_invitations_invited_by_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_booking_invitations')),
        sa.UniqueConstraint('token_hash', name=op.f('uq_booking_invitations_token_hash')),
    )
    op.create_index(op.f('ix_booking_invitations_email'), 'booking_invitations', ['email'])
    op.create_index(op.f('ix_booking_invitations_invited_by_id'), 'booking_invitations', ['invited_by_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_booking_invitations_invited_by_id'), table_name='booking_invitations')
    op.drop_index(op.f('ix_booking_invitations_email'), table_name='booking_invitations')
    op.drop_table('booking_invitations')
