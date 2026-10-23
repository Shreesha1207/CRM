"""payments

Online payment for bookings, through a payment provider (a demo one until a
real gateway is connected). A booking points at its payment; a reschedule
copies the link, so the payment follows the booking.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-23 09:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('payments',
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'PAID', 'EXPIRED', 'CANCELLED', 'REFUND_DUE', 'REFUNDED', name='paymentstatus', native_enum=False, length=32), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('provider_ref', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('refund_due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('refunded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('refunded_by', sa.UUID(), nullable=True),
    sa.Column('refund_note', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint('amount > 0', name=op.f('ck_payments_amount_positive')),
    sa.ForeignKeyConstraint(['refunded_by'], ['users.id'], name=op.f('fk_payments_refunded_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_payments_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payments'))
    )
    op.create_index(op.f('ix_payments_status'), 'payments', ['status'], unique=False)
    op.create_index(op.f('ix_payments_user_id'), 'payments', ['user_id'], unique=False)
    op.create_index('uq_payments_provider_ref', 'payments', ['provider', 'provider_ref'], unique=True, postgresql_where=sa.text('provider_ref IS NOT NULL'))
    op.add_column('bookings', sa.Column('payment_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_bookings_payment_id'), 'bookings', ['payment_id'], unique=False)
    op.create_foreign_key(op.f('fk_bookings_payment_id_payments'), 'bookings', 'payments', ['payment_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_bookings_payment_id_payments'), 'bookings', type_='foreignkey')
    op.drop_index(op.f('ix_bookings_payment_id'), table_name='bookings')
    op.drop_column('bookings', 'payment_id')
    op.drop_index('uq_payments_provider_ref', table_name='payments', postgresql_where=sa.text('provider_ref IS NOT NULL'))
    op.drop_index(op.f('ix_payments_user_id'), table_name='payments')
    op.drop_index(op.f('ix_payments_status'), table_name='payments')
    op.drop_table('payments')
