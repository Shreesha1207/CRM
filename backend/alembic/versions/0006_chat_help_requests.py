"""chat help requests

A customer can ask the admin to contact them from the booking assistant: the
request keeps their description and a snapshot of the whole chat. Notifications
get a link, for those that lead somewhere other than a booking.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19 09:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('chat_help_requests',
    sa.Column('conversation_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('transcript', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.Enum('OPEN', 'RESOLVED', name='chathelpstatus', native_enum=False, length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reminded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_by_id', sa.UUID(), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['chat_conversations.id'], name=op.f('fk_chat_help_requests_conversation_id_chat_conversations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resolved_by_id'], ['users.id'], name=op.f('fk_chat_help_requests_resolved_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_chat_help_requests_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_help_requests'))
    )
    op.create_index(op.f('ix_chat_help_requests_conversation_id'), 'chat_help_requests', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_chat_help_requests_due_at'), 'chat_help_requests', ['due_at'], unique=False)
    op.create_index(op.f('ix_chat_help_requests_user_id'), 'chat_help_requests', ['user_id'], unique=False)
    op.create_index('uq_chat_help_requests_open', 'chat_help_requests', ['user_id'], unique=True, postgresql_where=sa.text("status = 'OPEN'"))
    op.add_column('notifications', sa.Column('link', sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column('notifications', 'link')
    op.drop_index('uq_chat_help_requests_open', table_name='chat_help_requests', postgresql_where=sa.text("status = 'OPEN'"))
    op.drop_index(op.f('ix_chat_help_requests_user_id'), table_name='chat_help_requests')
    op.drop_index(op.f('ix_chat_help_requests_due_at'), table_name='chat_help_requests')
    op.drop_index(op.f('ix_chat_help_requests_conversation_id'), table_name='chat_help_requests')
    op.drop_table('chat_help_requests')
