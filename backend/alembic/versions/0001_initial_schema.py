"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-09-24 07:53:56.062592
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # btree_gist lets the booking_resources exclusion constraint combine
    # equality on resource_id / allocation_key with range overlap.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table('locations',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('status', sa.Enum('ACTIVE', 'INACTIVE', name='recordstatus', native_enum=False, length=32), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_locations'))
    )
    op.create_table('services',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('duration_minutes', sa.Integer(), nullable=False),
    sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('capacity', sa.Integer(), nullable=True),
    sa.Column('booking_type', sa.Enum('INDIVIDUAL', 'CAPACITY', name='bookingtype', native_enum=False, length=32), nullable=False),
    sa.Column('buffer_before', sa.Integer(), nullable=True),
    sa.Column('buffer_after', sa.Integer(), nullable=True),
    sa.Column('status', sa.Enum('ACTIVE', 'INACTIVE', name='recordstatus', native_enum=False, length=32), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('buffer_after IS NULL OR buffer_after >= 0', name=op.f('ck_services_buffer_after_nonneg')),
    sa.CheckConstraint('buffer_before IS NULL OR buffer_before >= 0', name=op.f('ck_services_buffer_before_nonneg')),
    sa.CheckConstraint('capacity IS NULL OR capacity > 0', name=op.f('ck_services_capacity_positive')),
    sa.CheckConstraint('duration_minutes > 0', name=op.f('ck_services_duration_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_services'))
    )
    op.create_table('users',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('phone', sa.String(length=50), nullable=True),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.Enum('USER', 'ADMIN', 'STAFF', 'RESOURCE_OWNER', 'MANAGER', 'SUPER_ADMIN', name='role', native_enum=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('ACTIVE', 'INACTIVE', 'SUSPENDED', name='userstatus', native_enum=False, length=32), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_table('audit_logs',
    sa.Column('actor_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('entity_type', sa.String(length=64), nullable=False),
    sa.Column('entity_id', sa.String(length=64), nullable=True),
    sa.Column('old_value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('new_value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_audit_logs_actor_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_actor_id'), 'audit_logs', ['actor_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_entity_id'), 'audit_logs', ['entity_id'], unique=False)
    op.create_table('auth_sessions',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_agent', sa.String(length=500), nullable=True),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_sessions'))
    )
    op.create_index(op.f('ix_auth_sessions_user_id'), 'auth_sessions', ['user_id'], unique=False)
    op.create_table('operating_hours',
    sa.Column('location_id', sa.UUID(), nullable=True),
    sa.Column('day_of_week', sa.Integer(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=False),
    sa.Column('end_time', sa.Time(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint('day_of_week BETWEEN 0 AND 6', name=op.f('ck_operating_hours_day_of_week_range')),
    sa.ForeignKeyConstraint(['location_id'], ['locations.id'], name=op.f('fk_operating_hours_location_id_locations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operating_hours'))
    )
    op.create_index(op.f('ix_operating_hours_location_id'), 'operating_hours', ['location_id'], unique=False)
    op.create_table('password_reset_tokens',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=128), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_password_reset_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_password_reset_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_password_reset_tokens_token_hash'))
    )
    op.create_index(op.f('ix_password_reset_tokens_user_id'), 'password_reset_tokens', ['user_id'], unique=False)
    op.create_table('resources',
    sa.Column('location_id', sa.UUID(), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('type', sa.Enum('PERSON', 'ROOM', 'FACILITY', 'EQUIPMENT', 'VEHICLE', 'DESK', 'COURT', 'CUSTOM', name='resourcetype', native_enum=False, length=32), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('capacity', sa.Integer(), nullable=True),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.Enum('ACTIVE', 'INACTIVE', name='recordstatus', native_enum=False, length=32), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('capacity IS NULL OR capacity > 0', name=op.f('ck_resources_capacity_positive')),
    sa.ForeignKeyConstraint(['location_id'], ['locations.id'], name=op.f('fk_resources_location_id_locations'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_resources'))
    )
    op.create_index(op.f('ix_resources_location_id'), 'resources', ['location_id'], unique=False)
    op.create_table('settings',
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_settings_updated_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_settings'))
    )
    op.create_table('availability_exceptions',
    sa.Column('resource_id', sa.UUID(), nullable=True),
    sa.Column('location_id', sa.UUID(), nullable=True),
    sa.Column('start_datetime', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_datetime', sa.DateTime(timezone=True), nullable=False),
    sa.Column('type', sa.Enum('UNAVAILABLE', 'BLOCKED', 'SPECIAL_HOURS', 'HOLIDAY', 'MAINTENANCE', name='exceptiontype', native_enum=False, length=32), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint('end_datetime > start_datetime', name=op.f('ck_availability_exceptions_range_valid')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_availability_exceptions_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['location_id'], ['locations.id'], name=op.f('fk_availability_exceptions_location_id_locations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resource_id'], ['resources.id'], name=op.f('fk_availability_exceptions_resource_id_resources'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_availability_exceptions'))
    )
    op.create_index(op.f('ix_availability_exceptions_location_id'), 'availability_exceptions', ['location_id'], unique=False)
    op.create_index('ix_availability_exceptions_range', 'availability_exceptions', ['start_datetime', 'end_datetime'], unique=False)
    op.create_index(op.f('ix_availability_exceptions_resource_id'), 'availability_exceptions', ['resource_id'], unique=False)
    op.create_table('recurring_series',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('service_id', sa.UUID(), nullable=False),
    sa.Column('resource_id', sa.UUID(), nullable=False),
    sa.Column('frequency', sa.Enum('DAILY', 'WEEKLY', 'MONTHLY', name='recurrencefrequency', native_enum=False, length=32), nullable=True),
    sa.Column('interval', sa.Integer(), nullable=False),
    sa.Column('occurrences', sa.Integer(), nullable=False),
    sa.Column('first_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_recurring_series_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['resource_id'], ['resources.id'], name=op.f('fk_recurring_series_resource_id_resources')),
    sa.ForeignKeyConstraint(['service_id'], ['services.id'], name=op.f('fk_recurring_series_service_id_services')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_recurring_series_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_recurring_series'))
    )
    op.create_index(op.f('ix_recurring_series_user_id'), 'recurring_series', ['user_id'], unique=False)
    op.create_table('resource_availability',
    sa.Column('resource_id', sa.UUID(), nullable=False),
    sa.Column('day_of_week', sa.Integer(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=False),
    sa.Column('end_time', sa.Time(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=True),
    sa.Column('valid_until', sa.Date(), nullable=True),
    sa.Column('is_available', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint('day_of_week BETWEEN 0 AND 6', name=op.f('ck_resource_availability_day_of_week_range')),
    sa.ForeignKeyConstraint(['resource_id'], ['resources.id'], name=op.f('fk_resource_availability_resource_id_resources'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_resource_availability'))
    )
    op.create_index(op.f('ix_resource_availability_resource_id'), 'resource_availability', ['resource_id'], unique=False)
    op.create_table('resource_services',
    sa.Column('resource_id', sa.UUID(), nullable=False),
    sa.Column('service_id', sa.UUID(), nullable=False),
    sa.Column('custom_duration', sa.Integer(), nullable=True),
    sa.Column('custom_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('custom_buffer_before', sa.Integer(), nullable=True),
    sa.Column('custom_buffer_after', sa.Integer(), nullable=True),
    sa.Column('status', sa.Enum('ACTIVE', 'INACTIVE', name='recordstatus', native_enum=False, length=32), nullable=False),
    sa.ForeignKeyConstraint(['resource_id'], ['resources.id'], name=op.f('fk_resource_services_resource_id_resources'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['service_id'], ['services.id'], name=op.f('fk_resource_services_service_id_services'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('resource_id', 'service_id', name=op.f('pk_resource_services'))
    )
    op.create_table('bookings',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('service_id', sa.UUID(), nullable=False),
    sa.Column('primary_resource_id', sa.UUID(), nullable=False),
    sa.Column('location_id', sa.UUID(), nullable=True),
    sa.Column('start_datetime', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_datetime', sa.DateTime(timezone=True), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'CONFIRMED', 'COMPLETED', 'CANCELLED', 'RESCHEDULED', 'NO_SHOW', 'WAITLISTED', 'CONFLICTED', name='bookingstatus', native_enum=False, length=32), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_by', sa.UUID(), nullable=True),
    sa.Column('cancellation_reason', sa.Text(), nullable=True),
    sa.Column('conflict_reason', sa.Text(), nullable=True),
    sa.Column('rescheduled_from_id', sa.UUID(), nullable=True),
    sa.Column('recurring_series_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('end_datetime > start_datetime', name=op.f('ck_bookings_range_valid')),
    sa.CheckConstraint('quantity > 0', name=op.f('ck_bookings_quantity_positive')),
    sa.ForeignKeyConstraint(['cancelled_by'], ['users.id'], name=op.f('fk_bookings_cancelled_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_bookings_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['location_id'], ['locations.id'], name=op.f('fk_bookings_location_id_locations'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['primary_resource_id'], ['resources.id'], name=op.f('fk_bookings_primary_resource_id_resources')),
    sa.ForeignKeyConstraint(['recurring_series_id'], ['recurring_series.id'], name=op.f('fk_bookings_recurring_series_id_recurring_series'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['rescheduled_from_id'], ['bookings.id'], name=op.f('fk_bookings_rescheduled_from_id_bookings'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['service_id'], ['services.id'], name=op.f('fk_bookings_service_id_services')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_bookings_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bookings'))
    )
    op.create_index(op.f('ix_bookings_primary_resource_id'), 'bookings', ['primary_resource_id'], unique=False)
    op.create_index(op.f('ix_bookings_recurring_series_id'), 'bookings', ['recurring_series_id'], unique=False)
    op.create_index(op.f('ix_bookings_rescheduled_from_id'), 'bookings', ['rescheduled_from_id'], unique=False)
    op.create_index('ix_bookings_resource_start', 'bookings', ['primary_resource_id', 'start_datetime'], unique=False)
    op.create_index(op.f('ix_bookings_service_id'), 'bookings', ['service_id'], unique=False)
    op.create_index('ix_bookings_start', 'bookings', ['start_datetime'], unique=False)
    op.create_index(op.f('ix_bookings_status'), 'bookings', ['status'], unique=False)
    op.create_index(op.f('ix_bookings_user_id'), 'bookings', ['user_id'], unique=False)
    op.create_table('booking_resources',
    sa.Column('booking_id', sa.UUID(), nullable=False),
    sa.Column('resource_id', sa.UUID(), nullable=False),
    sa.Column('occupied_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('occupied_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('allocation_key', sa.String(length=200), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    postgresql.ExcludeConstraint((sa.column('resource_id'), '='), (sa.text("tstzrange(occupied_start, occupied_end, '[)')"), '&&'), (sa.column('allocation_key'), '<>'), where=sa.text('active'), using='gist', name='ex_booking_resources_no_overlap'),
    sa.CheckConstraint('occupied_end > occupied_start', name=op.f('ck_booking_resources_range_valid')),
    sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], name=op.f('fk_booking_resources_booking_id_bookings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resource_id'], ['resources.id'], name=op.f('fk_booking_resources_resource_id_resources')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_booking_resources'))
    )
    op.create_index(op.f('ix_booking_resources_booking_id'), 'booking_resources', ['booking_id'], unique=False)
    op.create_index(op.f('ix_booking_resources_resource_id'), 'booking_resources', ['resource_id'], unique=False)
    op.create_index('ix_booking_resources_resource_range', 'booking_resources', ['resource_id', 'occupied_start', 'occupied_end'], unique=False)
    op.create_table('notifications',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('booking_id', sa.UUID(), nullable=True),
    sa.Column('type', sa.Enum('BOOKING_CREATED', 'BOOKING_CONFIRMED', 'BOOKING_RESCHEDULED', 'BOOKING_CANCELLED', 'BOOKING_REMINDER', 'WAITLIST_PROMOTION', 'RESOURCE_CHANGED', 'BOOKING_CONFLICTED', 'PASSWORD_RESET', name='notificationtype', native_enum=False, length=32), nullable=False),
    sa.Column('channel', sa.Enum('IN_APP', 'EMAIL', 'SMS', 'PUSH', name='notificationchannel', native_enum=False, length=32), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'SENT', 'FAILED', 'SKIPPED', name='notificationstatus', native_enum=False, length=32), nullable=False),
    sa.Column('dedupe_key', sa.String(length=300), nullable=True),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], name=op.f('fk_notifications_booking_id_bookings'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications')),
    sa.UniqueConstraint('dedupe_key', name=op.f('uq_notifications_dedupe_key'))
    )
    op.create_index(op.f('ix_notifications_booking_id'), 'notifications', ['booking_id'], unique=False)
    op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_notifications_user_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_booking_id'), table_name='notifications')
    op.drop_table('notifications')
    op.drop_index('ix_booking_resources_resource_range', table_name='booking_resources')
    op.drop_index(op.f('ix_booking_resources_resource_id'), table_name='booking_resources')
    op.drop_index(op.f('ix_booking_resources_booking_id'), table_name='booking_resources')
    op.drop_table('booking_resources')
    op.drop_index(op.f('ix_bookings_user_id'), table_name='bookings')
    op.drop_index(op.f('ix_bookings_status'), table_name='bookings')
    op.drop_index('ix_bookings_start', table_name='bookings')
    op.drop_index(op.f('ix_bookings_service_id'), table_name='bookings')
    op.drop_index('ix_bookings_resource_start', table_name='bookings')
    op.drop_index(op.f('ix_bookings_rescheduled_from_id'), table_name='bookings')
    op.drop_index(op.f('ix_bookings_recurring_series_id'), table_name='bookings')
    op.drop_index(op.f('ix_bookings_primary_resource_id'), table_name='bookings')
    op.drop_table('bookings')
    op.drop_table('resource_services')
    op.drop_index(op.f('ix_resource_availability_resource_id'), table_name='resource_availability')
    op.drop_table('resource_availability')
    op.drop_index(op.f('ix_recurring_series_user_id'), table_name='recurring_series')
    op.drop_table('recurring_series')
    op.drop_index(op.f('ix_availability_exceptions_resource_id'), table_name='availability_exceptions')
    op.drop_index('ix_availability_exceptions_range', table_name='availability_exceptions')
    op.drop_index(op.f('ix_availability_exceptions_location_id'), table_name='availability_exceptions')
    op.drop_table('availability_exceptions')
    op.drop_table('settings')
    op.drop_index(op.f('ix_resources_location_id'), table_name='resources')
    op.drop_table('resources')
    op.drop_index(op.f('ix_password_reset_tokens_user_id'), table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')
    op.drop_index(op.f('ix_operating_hours_location_id'), table_name='operating_hours')
    op.drop_table('operating_hours')
    op.drop_index(op.f('ix_auth_sessions_user_id'), table_name='auth_sessions')
    op.drop_table('auth_sessions')
    op.drop_index(op.f('ix_audit_logs_entity_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_created_at'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_actor_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_table('services')
    op.drop_table('locations')
