import hashlib
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings
from app.core.timeutils import utcnow

_hasher = PasswordHasher()

# Pre-computed so logins for unknown e-mails take as long as real ones.
_DUMMY_HASH = _hasher.hash("timing-equaliser")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def create_access_token(user_id: uuid.UUID, session_id: uuid.UUID, expires_at: datetime) -> str:
    settings = get_settings()
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "exp": int(expires_at.timestamp()),
        "iat": int(utcnow().timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "sid"], "verify_exp": False, "verify_iat": False},
        )
    except jwt.PyJWTError:
        return None
    # Expiry is checked against the application clock (see timeutils.utcnow).
    if payload["exp"] <= utcnow().timestamp():
        return None
    return payload


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())


class RateLimiter:
    """Sliding-window limiter kept in process memory.

    Good for a single instance; swap for Redis when running several replicas.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()
