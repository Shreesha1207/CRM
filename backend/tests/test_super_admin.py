from datetime import timedelta

from sqlalchemy import select

from app.core.permissions import Permission, can_assign_role, can_manage_account, permissions_for
from app.core.timeutils import freeze_time
from app.models import AuditLog, Booking, User
from app.models.enums import Role
from tests.conftest import MONDAY, NOW, at, auth_headers


def super_admin_headers(client, make):
    make.user("boss@test.io", Role.SUPER_ADMIN, name="Boss")
    return auth_headers(client, "boss@test.io")


def new_user(client, headers, role="USER", email="new@test.io"):
    return client.post(
        "/api/admin/users", headers=headers, json={"name": "New", "email": email, "role": role}
    )


def test_super_admin_has_every_admin_permission_and_more() -> None:
    assert permissions_for(Role.ADMIN) < permissions_for(Role.SUPER_ADMIN)
    assert Permission.ADMINS_MANAGE not in permissions_for(Role.ADMIN)
    assert Permission.USERS_DELETE not in permissions_for(Role.MANAGER)
    assert can_assign_role(Role.SUPER_ADMIN, Role.ADMIN) and not can_assign_role(Role.ADMIN, Role.ADMIN)
    assert can_assign_role(Role.ADMIN, Role.USER)
    # Nobody hands out the super admin role, and a super admin's account is its owner's alone.
    assert not any(can_assign_role(role, Role.SUPER_ADMIN) for role in Role)
    assert not any(can_manage_account(role, Role.SUPER_ADMIN) for role in Role)
    assert all(can_assign_role(Role.SUPER_ADMIN, role) for role in Role if role != Role.SUPER_ADMIN)


def test_super_admin_can_use_the_admin_console(client, make, basic) -> None:
    headers = super_admin_headers(client, make)
    me = client.get("/api/auth/me", headers=headers).json()
    assert {"bookings:manage", "settings:manage", "admins:manage", "users:delete"} <= set(me["permissions"])
    assert client.get("/api/admin/bookings", headers=headers).status_code == 200
    assert client.get("/api/admin/settings", headers=headers).status_code == 200


def test_only_a_super_admin_makes_admins(client, make, user, admin_headers) -> None:
    headers = super_admin_headers(client, make)
    assert new_user(client, admin_headers, "ADMIN").status_code == 403
    assert client.put(f"/api/admin/users/{user.id}", headers=admin_headers, json={"role": "ADMIN"}).status_code == 403

    promoted = client.put(f"/api/admin/users/{user.id}", headers=headers, json={"role": "ADMIN"})
    assert promoted.status_code == 200 and promoted.json()["role"] == "ADMIN"
    assert new_user(client, headers, "ADMIN").status_code == 201
    # ...and takes it away again.
    assert client.put(f"/api/admin/users/{user.id}", headers=headers, json={"role": "USER"}).json()["role"] == "USER"


def test_an_admin_cannot_change_another_admins_account(client, make, admin, admin_headers) -> None:
    other = make.user("other-admin@test.io", Role.ADMIN, name="Other")
    for change in ({"status": "SUSPENDED"}, {"name": "Renamed"}):
        assert client.put(f"/api/admin/users/{other.id}", headers=admin_headers, json=change).status_code == 403
    # Their own account is still theirs to edit.
    assert client.put(f"/api/admin/users/{admin.id}", headers=admin_headers, json={"name": "Me"}).status_code == 200
    # A super admin can.
    headers = super_admin_headers(client, make)
    assert client.put(f"/api/admin/users/{other.id}", headers=headers, json={"status": "SUSPENDED"}).status_code == 200


def test_super_admin_deletes_users_and_admins(client, db, make, admin, user, admin_headers) -> None:
    headers = super_admin_headers(client, make)
    # Admins cannot delete anyone.
    assert client.delete(f"/api/admin/users/{user.id}", headers=admin_headers).status_code == 403

    ids, emails = (user.id, admin.id), {user.email, admin.email}
    for user_id in ids:
        assert client.delete(f"/api/admin/users/{user_id}", headers=headers).status_code == 204
    db.expire_all()
    assert all(db.get(User, user_id) is None for user_id in ids)
    deleted = db.scalars(select(AuditLog).where(AuditLog.action == "USER_DELETED")).all()
    assert {d.old_value["email"] for d in deleted} == emails
    assert client.delete(f"/api/admin/users/{ids[0]}", headers=headers).status_code == 404


def test_a_super_admin_cannot_delete_themselves(client, db, make) -> None:
    headers = super_admin_headers(client, make)
    me = client.get("/api/auth/me", headers=headers).json()
    assert client.delete(f"/api/admin/users/{me['id']}", headers=headers).status_code == 400


def test_upcoming_bookings_block_deletion_but_past_ones_go_with_the_account(client, db, make, basic, user, user_headers) -> None:
    service, resource = basic
    headers = super_admin_headers(client, make)
    booked = client.post(
        "/api/bookings",
        headers=user_headers,
        json={"service_id": str(service.id), "resource_id": str(resource.id), "start": at(MONDAY, 10)},
    )
    assert booked.status_code == 201, booked.text

    refused = client.delete(f"/api/admin/users/{user.id}", headers=headers)
    assert refused.status_code == 409 and refused.json()["code"] == "HAS_UPCOMING_BOOKINGS"

    # Once the booking is in the past, the account and its history can go.
    freeze_time(NOW + timedelta(days=7))
    assert client.delete(f"/api/admin/users/{user.id}", headers=headers).status_code == 204
    db.expire_all()
    assert db.scalars(select(Booking)).all() == []


def test_nobody_can_make_a_super_admin_in_the_app(client, make, user, admin_headers) -> None:
    headers = super_admin_headers(client, make)
    for h in (headers, admin_headers):
        refused = new_user(client, h, "SUPER_ADMIN")
        assert refused.status_code == 403 and "super admin" in refused.json()["detail"]
        assert client.put(f"/api/admin/users/{user.id}", headers=h, json={"role": "SUPER_ADMIN"}).status_code == 403


def test_a_super_admins_account_is_left_to_its_owner(client, db, make) -> None:
    headers = super_admin_headers(client, make)
    other = make.user("boss2@test.io", Role.SUPER_ADMIN, name="Other boss")
    for change in ({"role": "USER"}, {"status": "SUSPENDED"}, {"name": "X"}):
        assert client.put(f"/api/admin/users/{other.id}", headers=headers, json=change).status_code == 403
    assert client.delete(f"/api/admin/users/{other.id}", headers=headers).status_code == 403
    # Their own name and password are still theirs to change.
    me = client.get("/api/auth/me", headers=headers).json()
    assert client.put(f"/api/admin/users/{me['id']}", headers=headers, json={"name": "Big Boss"}).status_code == 200


def test_an_admin_only_makes_customers(client, user, admin_headers) -> None:
    for role in ("STAFF", "RESOURCE_OWNER", "MANAGER", "ADMIN"):
        assert new_user(client, admin_headers, role, email=f"{role.lower()}@test.io").status_code == 403
    assert new_user(client, admin_headers, "USER").status_code == 201
