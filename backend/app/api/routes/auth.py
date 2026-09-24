import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update

from app.api.deps import ACCESS_COOKIE, CSRF_COOKIE, DB, CurrentUser, rate_limit
from app.core.config import get_settings
from app.core.permissions import permissions_for
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    needs_rehash,
    new_token,
    verify_password,
)
from app.core.timeutils import utcnow
from app.models import AuthSession, PasswordResetToken, User
from app.models.enums import Role, UserStatus
from app.schemas import (
    ChangePasswordIn,
    ForgotPasswordIn,
    LoginIn,
    MeOut,
    ProfileIn,
    RegisterIn,
    ResetPasswordIn,
    TokenOut,
)
from app.services import audit
from app.services.notifications import send_email

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger("auth")


def me_out(user: User) -> MeOut:
    return MeOut(
        **{k: getattr(user, k) for k in ("id", "name", "email", "phone", "role", "status", "created_at")},
        permissions=sorted(p.value for p in permissions_for(user.role)),
    )


def _start_session(db: DB, user: User, request: Request, response: Response) -> TokenOut:
    settings = get_settings()
    expires_at = utcnow() + timedelta(minutes=settings.access_token_ttl_minutes)
    session = AuthSession(
        user_id=user.id,
        expires_at=expires_at,
        user_agent=(request.headers.get("user-agent") or "")[:500],
        ip_address=request.client.host if request.client else None,
    )
    db.add(session)
    db.flush()
    token = create_access_token(user.id, session.id, expires_at)
    csrf = new_token()
    db.commit()

    max_age = settings.access_token_ttl_minutes * 60
    response.set_cookie(
        ACCESS_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        domain=settings.cookie_domain,
        path="/",
    )
    # Readable by the SPA so it can echo it back in the X-CSRF-Token header.
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        domain=settings.cookie_domain,
        path="/",
    )
    return TokenOut(access_token=token, expires_at=expires_at, csrf_token=csrf, user=me_out(user))


@router.post(
    "/register",
    response_model=TokenOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("register", 10, 3600))],
)
def register(body: RegisterIn, request: Request, response: Response, db: DB) -> TokenOut:
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this e-mail already exists")
    # Self-registration always yields a plain USER; roles are granted by admins.
    user = User(
        name=body.name.strip(),
        email=email,
        phone=body.phone,
        password_hash=hash_password(body.password),
        role=Role.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    db.flush()
    audit.record(db, actor_id=user.id, action="USER_REGISTERED", entity_type="user", entity_id=user.id)
    return _start_session(db, user, request, response)


@router.post("/login", response_model=TokenOut, dependencies=[Depends(rate_limit("login", 10, 300))])
def login(body: LoginIn, request: Request, response: Response, db: DB) -> TokenOut:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not verify_password(body.password, user.password_hash if user else None) or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid e-mail or password")
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is not active")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    return _start_session(db, user, request, response)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, user: CurrentUser, db: DB) -> Response:
    session_id = getattr(request.state, "session_id", None)
    if session_id:
        db.execute(update(AuthSession).where(AuthSession.id == session_id).values(revoked_at=utcnow()))
        db.commit()
    settings = get_settings()
    response.status_code = status.HTTP_204_NO_CONTENT
    response.delete_cookie(ACCESS_COOKIE, path="/", domain=settings.cookie_domain)
    response.delete_cookie(CSRF_COOKIE, path="/", domain=settings.cookie_domain)
    return response


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("forgot", 5, 900))],
)
def forgot_password(body: ForgotPasswordIn, db: DB) -> dict:
    settings = get_settings()
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is not None and user.status == UserStatus.ACTIVE:
        token = new_token()
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=utcnow() + timedelta(minutes=settings.password_reset_ttl_minutes),
            )
        )
        db.commit()
        link = f"{settings.frontend_url}/reset-password?token={token}"
        try:
            send_email(
                user.email,
                "Reset your password",
                f"Hi {user.name},\n\nUse this link to choose a new password "
                f"(valid for {settings.password_reset_ttl_minutes} minutes):\n{link}\n\n"
                "If you did not ask for this, ignore this e-mail.",
            )
        except Exception:
            log.exception("Could not send password reset e-mail")
    # Same answer whether or not the account exists: no e-mail enumeration.
    return {"message": "If that e-mail is registered, a reset link has been sent."}


@router.post("/reset-password", dependencies=[Depends(rate_limit("reset", 10, 900))])
def reset_password(body: ResetPasswordIn, db: DB) -> dict:
    record = db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(body.token)).with_for_update()
    )
    now = utcnow()
    if record is None or record.used_at is not None or record.expires_at <= now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired")
    user = db.get(User, record.user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired")
    user.password_hash = hash_password(body.password)
    record.used_at = now
    # Sign out every existing session.
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    audit.record(db, actor_id=user.id, action="PASSWORD_RESET", entity_type="user", entity_id=user.id)
    db.commit()
    return {"message": "Your password has been reset. Please sign in."}


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser) -> MeOut:
    return me_out(user)


@router.put("/me", response_model=MeOut)
def update_me(body: ProfileIn, user: CurrentUser, db: DB) -> MeOut:
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(user, key, value.strip() if isinstance(value, str) else value)
    db.commit()
    return me_out(user)


@router.post("/change-password", dependencies=[Depends(rate_limit("change-password", 10, 900))])
def change_password(body: ChangePasswordIn, request: Request, user: CurrentUser, db: DB) -> dict:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    # Keep this session, end all others.
    current = getattr(request.state, "session_id", None)
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None), AuthSession.id != current)
        .values(revoked_at=utcnow())
    )
    audit.record(db, actor_id=user.id, action="PASSWORD_CHANGED", entity_type="user", entity_id=user.id)
    db.commit()
    return {"message": "Password updated"}
