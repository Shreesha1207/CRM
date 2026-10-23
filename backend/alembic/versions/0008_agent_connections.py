"""agent connections

Customers can connect their own AI assistants (Claude, ChatGPT, Gemini, a Siri
Shortcut...) through OAuth or a personal access token. Only token hashes are
stored. Each connection gets a hidden conversation for its holds, offered
changes and help requests.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-23 09:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('agent_clients',
    sa.Column('client_name', sa.String(length=100), nullable=False),
    sa.Column('redirect_uris', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('token_endpoint_auth_method', sa.String(length=32), nullable=False),
    sa.Column('client_secret_hash', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_clients'))
    )
    op.create_table('agent_connections',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('client_id', sa.UUID(), nullable=True),
    sa.Column('kind', sa.Enum('OAUTH', 'TOKEN', name='agentconnectionkind', native_enum=False, length=32), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('scopes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('weekly_spend_limit', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['agent_clients.id'], name=op.f('fk_agent_connections_client_id_agent_clients'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_agent_connections_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_connections'))
    )
    op.create_index(op.f('ix_agent_connections_client_id'), 'agent_connections', ['client_id'], unique=False)
    op.create_index(op.f('ix_agent_connections_user_id'), 'agent_connections', ['user_id'], unique=False)
    op.create_index('uq_agent_connections_client', 'agent_connections', ['user_id', 'client_id'], unique=True, postgresql_where=sa.text('revoked_at IS NULL AND client_id IS NOT NULL'))
    op.create_table('agent_tokens',
    sa.Column('connection_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.Enum('CODE', 'ACCESS', 'REFRESH', name='agenttokenkind', native_enum=False, length=32), nullable=False),
    sa.Column('token_hash', sa.String(length=128), nullable=False),
    sa.Column('resource', sa.String(length=500), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['connection_id'], ['agent_connections.id'], name=op.f('fk_agent_tokens_connection_id_agent_connections'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_agent_tokens_token_hash'))
    )
    op.create_index(op.f('ix_agent_tokens_connection_id'), 'agent_tokens', ['connection_id'], unique=False)
    op.add_column('chat_conversations', sa.Column('agent_connection_id', sa.UUID(), nullable=True))
    op.create_unique_constraint(op.f('uq_chat_conversations_agent_connection_id'), 'chat_conversations', ['agent_connection_id'])
    op.create_foreign_key(op.f('fk_chat_conversations_agent_connection_id_agent_connections'), 'chat_conversations', 'agent_connections', ['agent_connection_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_chat_conversations_agent_connection_id_agent_connections'), 'chat_conversations', type_='foreignkey')
    op.drop_constraint(op.f('uq_chat_conversations_agent_connection_id'), 'chat_conversations', type_='unique')
    op.drop_column('chat_conversations', 'agent_connection_id')
    op.drop_index(op.f('ix_agent_tokens_connection_id'), table_name='agent_tokens')
    op.drop_table('agent_tokens')
    op.drop_index('uq_agent_connections_client', table_name='agent_connections', postgresql_where=sa.text('revoked_at IS NULL AND client_id IS NOT NULL'))
    op.drop_index(op.f('ix_agent_connections_user_id'), table_name='agent_connections')
    op.drop_index(op.f('ix_agent_connections_client_id'), table_name='agent_connections')
    op.drop_table('agent_connections')
    op.drop_table('agent_clients')
