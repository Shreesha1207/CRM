"""chat assistant

The customers' booking assistant: who has access to it (a demo paywall), the
conversations and their messages, and the bookings it holds or changes it
offers for the customer to confirm.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17 09:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'chat_conversations',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('busy_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('flagged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_chat_conversations_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_conversations')),
    )
    op.create_index(op.f('ix_chat_conversations_user_id'), 'chat_conversations', ['user_id'])
    op.create_table(
        'chat_subscriptions',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.Enum('ACTIVE', 'CANCELLED', name='chatsubscriptionstatus', native_enum=False, length=32), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('paused_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_chat_subscriptions_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_subscriptions')),
        sa.UniqueConstraint('user_id', name=op.f('uq_chat_subscriptions_user_id')),
    )
    op.create_table(
        'chat_messages',
        sa.Column('conversation_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('turn_id', sa.UUID(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('role', sa.Enum('USER', 'ASSISTANT', 'SYSTEM', 'NOTICE', name='chatrole', native_enum=False, length=32), nullable=False),
        sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('display', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('feedback', sa.Integer(), nullable=True),
        sa.Column('blocked_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.CheckConstraint('feedback IN (-1, 1)', name=op.f('ck_chat_messages_feedback_valid')),
        sa.ForeignKeyConstraint(['conversation_id'], ['chat_conversations.id'], name=op.f('fk_chat_messages_conversation_id_chat_conversations'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_chat_messages_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_messages')),
    )
    op.create_index(op.f('ix_chat_messages_conversation_id'), 'chat_messages', ['conversation_id'])
    op.create_index(op.f('ix_chat_messages_created_at'), 'chat_messages', ['created_at'])
    op.create_index(op.f('ix_chat_messages_turn_id'), 'chat_messages', ['turn_id'])
    op.create_index(op.f('ix_chat_messages_user_id'), 'chat_messages', ['user_id'])
    op.create_index('uq_chat_messages_conversation_seq', 'chat_messages', ['conversation_id', 'seq'], unique=True)
    op.create_table(
        'chat_proposals',
        sa.Column('conversation_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('turn_id', sa.UUID(), nullable=False),
        sa.Column('kind', sa.Enum('HOLD', 'RESCHEDULE', 'CANCEL', name='chatproposalkind', native_enum=False, length=32), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'CONFIRMED', 'DECLINED', 'EXPIRED', 'FAILED', name='chatproposalstatus', native_enum=False, length=32), nullable=False),
        sa.Column('booking_id', sa.UUID(), nullable=True),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('result_booking_id', sa.UUID(), nullable=True),
        sa.Column('result_message', sa.Text(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reported_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], name=op.f('fk_chat_proposals_booking_id_bookings'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['conversation_id'], ['chat_conversations.id'], name=op.f('fk_chat_proposals_conversation_id_chat_conversations'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['result_booking_id'], ['bookings.id'], name=op.f('fk_chat_proposals_result_booking_id_bookings'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_chat_proposals_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_proposals')),
    )
    op.create_index(op.f('ix_chat_proposals_booking_id'), 'chat_proposals', ['booking_id'])
    op.create_index(op.f('ix_chat_proposals_conversation_id'), 'chat_proposals', ['conversation_id'])
    op.create_index(op.f('ix_chat_proposals_turn_id'), 'chat_proposals', ['turn_id'])
    op.create_index(op.f('ix_chat_proposals_user_id'), 'chat_proposals', ['user_id'])
    op.create_index('uq_chat_proposals_open_hold', 'chat_proposals', ['booking_id'], unique=True, postgresql_where=sa.text("kind = 'HOLD' AND status = 'PENDING'"))


def downgrade() -> None:
    op.drop_index('uq_chat_proposals_open_hold', table_name='chat_proposals', postgresql_where=sa.text("kind = 'HOLD' AND status = 'PENDING'"))
    op.drop_index(op.f('ix_chat_proposals_user_id'), table_name='chat_proposals')
    op.drop_index(op.f('ix_chat_proposals_turn_id'), table_name='chat_proposals')
    op.drop_index(op.f('ix_chat_proposals_conversation_id'), table_name='chat_proposals')
    op.drop_index(op.f('ix_chat_proposals_booking_id'), table_name='chat_proposals')
    op.drop_table('chat_proposals')
    op.drop_index('uq_chat_messages_conversation_seq', table_name='chat_messages')
    op.drop_index(op.f('ix_chat_messages_user_id'), table_name='chat_messages')
    op.drop_index(op.f('ix_chat_messages_turn_id'), table_name='chat_messages')
    op.drop_index(op.f('ix_chat_messages_created_at'), table_name='chat_messages')
    op.drop_index(op.f('ix_chat_messages_conversation_id'), table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_table('chat_subscriptions')
    op.drop_index(op.f('ix_chat_conversations_user_id'), table_name='chat_conversations')
    op.drop_table('chat_conversations')
