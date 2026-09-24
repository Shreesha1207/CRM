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


_ALL = frozenset(Permission)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.USER: frozenset(),
    # Reserved roles, ready to be granted in future iterations.
    Role.RESOURCE_OWNER: frozenset({Permission.BOOKINGS_VIEW_ALL}),
    Role.STAFF: frozenset(
        {Permission.BOOKINGS_VIEW_ALL, Permission.BOOKINGS_MANAGE, Permission.REPORTS_VIEW}
    ),
    Role.MANAGER: _ALL - {Permission.SETTINGS_MANAGE, Permission.USERS_MANAGE},
    Role.ADMIN: _ALL,
    Role.SUPER_ADMIN: _ALL,
}

# Roles that may assign each role; only a SUPER_ADMIN can create another one.
ROLE_ASSIGNERS: dict[Role, frozenset[Role]] = {
    Role.SUPER_ADMIN: frozenset({Role.SUPER_ADMIN}),
}


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in permissions_for(role)


def can_assign_role(actor_role: Role, target_role: Role) -> bool:
    allowed = ROLE_ASSIGNERS.get(target_role)
    if allowed is None:
        return has_permission(actor_role, Permission.USERS_MANAGE)
    return actor_role in allowed


def is_staff(role: Role) -> bool:
    """Any role that can see the admin area."""
    return bool(permissions_for(role))
