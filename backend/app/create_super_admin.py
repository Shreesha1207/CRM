"""Create the super admin, or promote an existing account to one:

    python -m app.create_super_admin --email boss@example.com --name "Super Admin"

The password is read from SUPER_ADMIN_PASSWORD, or asked for, so it never has
to appear in the shell history. Only a super admin can make admins, so the
first one has to come from here (or from `python -m app.seed`).
"""

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import User
from app.models.enums import Role, UserStatus
from app.services import audit


def ensure_super_admin(email: str, password: str, name: str) -> str:
    email = email.strip().lower()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(name=name, email=email, password_hash=hash_password(password), role=Role.SUPER_ADMIN, status=UserStatus.ACTIVE)
            db.add(user)
            db.flush()
            outcome = "created"
        else:
            user.role = Role.SUPER_ADMIN
            user.status = UserStatus.ACTIVE
            user.password_hash = hash_password(password)
            outcome = "promoted"
        audit.record(db, actor_id=None, action="SUPER_ADMIN_SET", entity_type="user", entity_id=user.id, new={"email": email})
        db.commit()
        return outcome


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="Super Admin")
    args = parser.parse_args()
    password = os.getenv("SUPER_ADMIN_PASSWORD") or getpass.getpass("Password: ")
    if len(password) < 8:
        sys.exit("The password must be at least 8 characters.")
    print(f"Super admin {args.email}: {ensure_super_admin(args.email, password, args.name)}.")


if __name__ == "__main__":
    main()
