import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.auth import config


password_hasher = PasswordHasher()

# Used to spend the same time verifying when the email is unknown
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(hashed_password: str, password: str) -> bool:
    try:
        return password_hasher.verify(hashed_password, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed_password: str) -> bool:
    return password_hasher.check_needs_rehash(hashed_password)


def burn_password_check(password: str) -> None:
    verify_password(DUMMY_HASH, password)


def create_access_token(user, lifetime: timedelta) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires_at = now + lifetime

    payload = {
        "sub": str(user.id),
        "ver": user.token_version,
        "jti": uuid.uuid4().hex,
        "type": "access",
        "iat": now,
        "exp": expires_at,
    }

    token = jwt.encode(
        payload,
        config.JWT_SECRET_KEY,
        algorithm=config.JWT_ALGORITHM
    )
    return token, expires_at


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(
            token,
            config.JWT_SECRET_KEY,
            algorithms=[config.JWT_ALGORITHM],
            options={"require": ["sub", "ver", "jti", "exp", "type"]},
        )
    except jwt.PyJWTError:
        return None

    if payload.get("type") != "access":
        return None

    return payload


def generate_reset_token() -> tuple[str, str]:
    """Return the raw token for the email link and the hash to store."""
    token = secrets.token_urlsafe(32)
    return token, hash_reset_token(token)


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
