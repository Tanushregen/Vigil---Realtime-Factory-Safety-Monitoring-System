from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import config
from app.auth.models import RevokedToken, User
from app.auth.security import decode_access_token
from app.db import get_db


# Routes reachable without a session. Every other route requires login.
PUBLIC_PATHS = {
    "/",
    "/health",
    "/api/auth/config",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    # Meta calls this without a session; its verify token and request signature protect it
    "/api/whatsapp/webhook",
}

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def unauthorized(detail: str = "Not authenticated"):
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail
    )


def check_request_origin(request: Request):
    """Reject state-changing browser requests sent from other websites."""
    if request.method in SAFE_METHODS:
        return

    origin = request.headers.get("origin")
    if not origin:
        return

    own_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"

    if origin not in config.ALLOWED_ORIGINS and origin != own_origin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request origin not allowed"
        )


def authenticate_request(request: Request, db: Session) -> User:
    token = request.cookies.get(config.COOKIE_NAME)
    if not token:
        raise unauthorized()

    payload = decode_access_token(token)
    if payload is None:
        raise unauthorized("Session expired or invalid")

    revoked = db.query(RevokedToken).filter(
        RevokedToken.jti == payload["jti"]
    ).first()

    try:
        user = db.get(User, int(payload["sub"]))
    except (TypeError, ValueError):
        user = None

    if (
        revoked
        or not user
        or not user.is_active
        or user.token_version != payload["ver"]
    ):
        raise unauthorized("Session expired or invalid")

    request.state.user = user
    request.state.token_payload = payload
    return user


# Applied to every route in the app; skips the public paths
def enforce_authentication(
    request: Request,
    db: Session = Depends(get_db)
):
    check_request_origin(request)

    if request.url.path in PUBLIC_PATHS:
        return

    authenticate_request(request, db)


def get_current_user(
    request: Request,
    db: Session = Depends(get_db)
) -> User:
    user = getattr(request.state, "user", None)
    if user is not None:
        return user
    return authenticate_request(request, db)


def require_admin(
    user: User = Depends(get_current_user)
) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required"
        )
    return user
