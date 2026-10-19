"""Role -> permission mapping.

Routes check permissions, not role names, so new roles (STAFF, MANAGER...)
only need an entry here.
"""

from enum import StrEnum

from app.models.enums import Role


class Permission(StrEnum):
    BOOKINGS_VIEW_ALL = "bookings:view_all"
    BOOKINGS_MANAGE = "bookings:manage"
    OVERRIDE_RULES = "bookings:override_rules"
    CONFLICTS_MANAGE = "conflicts:manage"
    RESOURCES_MANAGE = "resources:manage"
    SERVICES_MANAGE = "services:manage"
    SCHEDULES_MANAGE = "schedules:manage"
    LOCATIONS_MANAGE = "locations:manage"
    USERS_VIEW = "users:view"
    USERS_MANAGE = "users:manage"
    SETTINGS_MANAGE = "settings:manage"
    REPORTS_VIEW = "reports:view"
    AUDIT_VIEW = "audit:view"
    # Read the chats customers sent to the admin, and close their requests.
    CHAT_REVIEW = "chat:review"
    # Super admin only: grant or take away admin-console roles, change admin accounts.
    ADMINS_MANAGE = "admins:manage"
    # Super admin only: delete accounts outright.
    USERS_DELETE = "users:delete"


_ALL = frozenset(Permission)
_SUPER_ONLY = frozenset({Permission.ADMINS_MANAGE, Permission.USERS_DELETE})

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.USER: frozenset(),
    # Reserved roles, ready to be granted in future iterations.
    Role.RESOURCE_OWNER: frozenset({Permission.BOOKINGS_VIEW_ALL}),
    Role.STAFF: frozenset(
        {Permission.BOOKINGS_VIEW_ALL, Permission.BOOKINGS_MANAGE, Permission.REPORTS_VIEW}
    ),
    Role.MANAGER: _ALL - _SUPER_ONLY - {Permission.SETTINGS_MANAGE, Permission.USERS_MANAGE},
    Role.ADMIN: _ALL - _SUPER_ONLY,
    # Everything an admin can do, plus deciding who the admins are.
    Role.SUPER_ADMIN: _ALL,
}


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in permissions_for(role)


def is_staff(role: Role) -> bool:
    """Any role that can see the admin area."""
    return bool(permissions_for(role))


def can_assign_role(actor_role: Role, target_role: Role) -> bool:
    """The hierarchy: an admin makes customers; a super admin makes customers and
    every admin-console role below their own. Nobody makes a super admin in the
    app: that is only done on the server, with `python -m app.create_super_admin`."""
    if target_role == Role.SUPER_ADMIN:
        return False
    if is_staff(target_role):
        return has_permission(actor_role, Permission.ADMINS_MANAGE)
    return has_permission(actor_role, Permission.USERS_MANAGE)


def can_manage_account(actor_role: Role, target_role: Role) -> bool:
    """Whether someone may change or delete another person's account: only the
    accounts below them. A super admin's account is left to its owner."""
    if target_role == Role.SUPER_ADMIN:
        return False
    if is_staff(target_role):
        return has_permission(actor_role, Permission.ADMINS_MANAGE)
    return has_permission(actor_role, Permission.USERS_MANAGE)

