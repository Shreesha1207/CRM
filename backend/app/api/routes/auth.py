import logging
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from google.auth.transport import requests
from google.oauth2 import id_token
from sqlalchemy import select, update

from app.api.deps import ACCESS_COOKIE, CSRF_COOKIE, DB, CurrentUser, OptionalUser, rate_limit
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
    DeleteAccountIn,
    ForgotPasswordIn,
    GoogleAuthIn,
    LoginIn,
    MeOut,
    ProfileIn,
    RegisterIn,
    ResetPasswordIn,
    TokenOut,
)
from app.services import accounts, audit
from app.services.agents import oauth

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger("auth")

GOOGLE_CLIENT_ID = "958098080029-l893tjb5g3l59f048i4pjj5qlgle93v2.apps.googleusercontent.com"


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
        # On the application clock: connecting an AI assistant needs a recent sign-in.
        created_at=utcnow(),
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
def register(body: RegisterIn, request: Request, response: Response, db: DB, background: BackgroundTasks) -> TokenOut:
    email = body.email.lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        if existing.status == UserStatus.INVITED:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This e-mail address has been invited: open the link in the invitation e-mail to set up your account",
            )
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
    token = _start_session(db, user, request, response)
    background.add_task(accounts.send_welcome, user.id)
    return token


@router.post("/login", response_model=TokenOut, dependencies=[Depends(rate_limit("login", 10, 300))])
def login(body: LoginIn, request: Request, response: Response, db: DB) -> TokenOut:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not verify_password(body.password, user.password_hash if user else None) or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid e-mail or password")
    if user.status == UserStatus.INVITED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Finish setting up your account first: open the link in your invitation e-mail")
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is not active")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    return _start_session(db, user, request, response)


@router.post("/google", response_model=TokenOut)
def google_auth(body: GoogleAuthIn, request: Request, response: Response, db: DB, background: BackgroundTasks) -> TokenOut:
    try:
        idinfo = id_token.verify_oauth2_token(body.credential, requests.Request(), GOOGLE_CLIENT_ID)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Google token") from None
    
    email = idinfo.get("email")
    name = idinfo.get("name", "Unknown Google User")
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email not found in Google account")
    # The e-mail decides which account this is, so it must be one Google has verified.
    if not idinfo.get("email_verified"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Verify this e-mail with Google first")

    user = db.scalar(select(User).where(User.email == email.lower()))
    if user:
        if user.status != UserStatus.ACTIVE:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is not active")
    else:
        # Like /register: new accounts are customers; an admin grants staff roles.
        user = User(
            name=name,
            email=email.lower(),
            password_hash=hash_password(new_token()), # Random dummy password
            role=Role.USER,
            status=UserStatus.ACTIVE,
        )
        db.add(user)
        db.flush()
        audit.record(db, actor_id=user.id, action="USER_REGISTERED_GOOGLE", entity_type="user", entity_id=user.id)
        background.add_task(accounts.send_welcome, user.id)
        
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
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    # An invited account that lost its e-mail can set itself up the same way.
    if user is not None and user.status in (UserStatus.ACTIVE, UserStatus.INVITED):
        try:
            accounts.send_reset_link(db, user)
            db.commit()
        except Exception:
            db.rollback()
            log.exception("Could not send password reset e-mail")
    # Same answer whether or not the account exists: no e-mail enumeration.
    return {"message": "If that e-mail is registered, a reset link has been sent."}


@router.post("/reset-password", dependencies=[Depends(rate_limit("reset", 10, 900))])
def reset_password(body: ResetPasswordIn, db: DB, background: BackgroundTasks) -> dict:
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
    setting_up = user.status == UserStatus.INVITED
    if setting_up:
        # They chose their own password, so the account is theirs now.
        accounts.set_status(user, UserStatus.ACTIVE)
    # Sign out every existing session.
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    audit.record(db, actor_id=user.id, action="ACCOUNT_SET_UP" if setting_up else "PASSWORD_RESET", entity_type="user", entity_id=user.id)
    db.commit()
    # Whoever had the account may have connected AI assistants to it: they stop too.
    oauth.revoke_all(db, user.id, reason="password reset")
    if setting_up:
        background.add_task(accounts.send_welcome, user.id)
        return {"message": "Your account is ready. Please sign in."}
    return {"message": "Your password has been reset. Please sign in."}


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser) -> MeOut:
    return me_out(user)


@router.get("/session")
def session(user: OptionalUser) -> dict:
    """Like /me, but answers 200 with ``user: null`` for anonymous visitors."""
    return {"user": me_out(user) if user else None}


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


@router.post("/delete-account", dependencies=[Depends(rate_limit("delete-account", 5, 900))])
def delete_account(body: DeleteAccountIn, response: Response, user: CurrentUser, db: DB) -> dict:
    """Delete your own account for good (your right to erasure). Upcoming bookings are
    cancelled; the account and its booking history are deleted; you are signed out."""
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password is incorrect")
    accounts.close_own_account(db, user)
    settings = get_settings()
    response.delete_cookie(ACCESS_COOKIE, path="/", domain=settings.cookie_domain)
    response.delete_cookie(CSRF_COOKIE, path="/", domain=settings.cookie_domain)
    return {"message": "Your account has been deleted."}
