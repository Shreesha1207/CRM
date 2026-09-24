import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.permissions import Permission, has_permission
from app.core.security import constant_time_equals, decode_access_token, rate_limiter
from app.core.timeutils import utcnow
from app.db.session import get_db
from app.models import AuthSession, User
from app.models.enums import UserStatus

ACCESS_COOKIE = "access_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DB = Annotated[Session, Depends(get_db)]


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def _token_from_request(request: Request) -> tuple[str | None, bool]:
    """Returns (token, came_from_cookie)."""
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip(), False
    cookie = request.cookies.get(ACCESS_COOKIE)
    return (cookie, True) if cookie else (None, False)


def _authenticate(request: Request, db: Session) -> User | None:
    token, from_cookie = _token_from_request(request)
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        raise _unauthorized("Invalid or expired token")

    # Cookies are sent automatically by the browser, so state-changing
    # requests authenticated by cookie must prove same-origin intent with the
    # double-submit CSRF token. Bearer tokens are not sent automatically and
    # need no CSRF check.
    if from_cookie and request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        cookie = request.cookies.get(CSRF_COOKIE, "")
        if not header or not cookie or not constant_time_equals(header, cookie):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")

    try:
        session_id = uuid.UUID(payload["sid"])
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise _unauthorized("Invalid token") from None
    session = db.get(AuthSession, session_id)
    if session is None or session.revoked_at is not None or session.expires_at <= utcnow() or session.user_id != user_id:
        raise _unauthorized("Session expired")
    user = db.get(User, user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise _unauthorized("Account is not active")
    request.state.session_id = session_id
    return user


def get_optional_user(request: Request, db: DB) -> User | None:
    return _authenticate(request, db)


def get_current_user(request: Request, db: DB) -> User:
    user = _authenticate(request, db)
    if user is None:
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


def require(permission: Permission) -> Callable[..., User]:
    """Dependency factory: the backend, not the UI, decides who may do what."""

    def dependency(user: CurrentUser) -> User:
        if not has_permission(user.role, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission to do that")
        return user

    return dependency


def rate_limit(name: str, limit: int, window_seconds: int) -> Callable[[Request], None]:
    def dependency(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        if not rate_limiter.hit(f"{name}:{client}", limit, window_seconds):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many requests, please try again later",
                headers={"Retry-After": str(window_seconds)},
            )

    return dependency
