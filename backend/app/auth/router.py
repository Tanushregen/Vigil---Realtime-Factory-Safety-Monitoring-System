import logging
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.auth import config
from app.auth.dependencies import get_current_user, require_admin
from app.auth.mailer import send_password_reset_email
from app.auth.models import PasswordResetToken, RevokedToken, User
from app.auth.rate_limit import AttemptLimiter
from app.auth.schemas import (
    AuthConfigResponse,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    ResetPasswordRequest,
    UserCreate,
    UserResponse,
    UserUpdate,
)
from app.auth.security import (
    burn_password_check,
    create_access_token,
    decode_access_token,
    generate_reset_token,
    hash_password,
    hash_reset_token,
    needs_rehash,
    verify_password,
)
from app.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

LOCKOUT_SECONDS = config.LOGIN_LOCKOUT_MINUTES * 60

email_login_limiter = AttemptLimiter(
    config.LOGIN_MAX_FAILURES_PER_EMAIL, LOCKOUT_SECONDS
)
ip_login_limiter = AttemptLimiter(
    config.LOGIN_MAX_FAILURES_PER_IP, LOCKOUT_SECONDS
)
email_reset_limiter = AttemptLimiter(3, LOCKOUT_SECONDS)
ip_reset_limiter = AttemptLimiter(10, LOCKOUT_SECONDS)

INVALID_CREDENTIALS = "Invalid email or password."
FORGOT_PASSWORD_MESSAGE = (
    "If an active account exists for that email, "
    "a password reset link has been sent."
)


def utc_now() -> datetime:
    # Stored as naive UTC, matching the existing tables
    return datetime.utcnow()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def too_many_attempts(retry_after: int):
    minutes = max(1, round(retry_after / 60))
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many attempts. Try again in {minutes} minute(s).",
        headers={"Retry-After": str(retry_after)},
    )


def set_session_cookie(response: Response, token: str, max_age: int | None):
    response.set_cookie(
        key=config.COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
        path="/",
    )


def clear_session_cookie(response: Response):
    response.delete_cookie(
        key=config.COOKIE_NAME,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
        path="/",
    )


# ---------------- SESSION APIs ----------------

@router.get("/config", response_model=AuthConfigResponse)
def auth_config():
    return {"password_reset_enabled": config.EMAIL_ENABLED}


@router.post("/login", response_model=LoginResponse)
def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    email_key = data.email
    ip_key = client_ip(request)

    retry_after = max(
        email_login_limiter.retry_after(email_key),
        ip_login_limiter.retry_after(ip_key),
    )
    if retry_after:
        raise too_many_attempts(retry_after)

    user = db.query(User).filter(User.email == data.email).first()

    if user:
        password_valid = verify_password(user.hashed_password, data.password)
    else:
        # Same hashing cost, so response time does not reveal unknown emails
        burn_password_check(data.password)
        password_valid = False

    if not password_valid:
        email_login_limiter.record_failure(email_key)
        ip_login_limiter.record_failure(ip_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS
        )

    # Only reachable with the correct password, so it reveals nothing new
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is disabled. Contact your administrator."
        )

    email_login_limiter.reset(email_key)

    if needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(data.password)

    user.last_login = utc_now()
    db.commit()
    db.refresh(user)

    if data.remember_me:
        lifetime = timedelta(days=config.REMEMBER_ME_DAYS)
        cookie_max_age = int(lifetime.total_seconds())
    else:
        # Session cookie: removed when the browser is closed
        lifetime = timedelta(hours=config.SESSION_HOURS)
        cookie_max_age = None

    token, expires_at = create_access_token(user, lifetime)
    set_session_cookie(response, token, cookie_max_age)

    return {"user": user, "expires_at": expires_at}


@router.get("/me", response_model=UserResponse)
def get_me(user: User = Depends(get_current_user)):
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    token = request.cookies.get(config.COOKIE_NAME)
    payload = decode_access_token(token) if token else None

    if payload:
        now = utc_now()

        # Drop revocations whose tokens have expired anyway
        db.query(RevokedToken).filter(
            RevokedToken.expires_at < now
        ).delete()

        exists = db.query(RevokedToken).filter(
            RevokedToken.jti == payload["jti"]
        ).first()

        if not exists:
            db.add(RevokedToken(
                jti=payload["jti"],
                expires_at=datetime.fromtimestamp(
                    payload["exp"], tz=timezone.utc
                ).replace(tzinfo=None)
            ))

        db.commit()

    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ---------------- PASSWORD RESET APIs ----------------

@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED
)
def forgot_password(
    data: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    if not config.EMAIL_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Password reset by email is not configured. "
                "Contact your administrator to reset your password."
            )
        )

    ip_key = client_ip(request)
    retry_after = ip_reset_limiter.retry_after(ip_key)
    if retry_after:
        raise too_many_attempts(retry_after)
    ip_reset_limiter.record_failure(ip_key)

    # Over the per-email limit: answer the same way but send nothing
    if email_reset_limiter.retry_after(data.email):
        return {"message": FORGOT_PASSWORD_MESSAGE}
    email_reset_limiter.record_failure(data.email)

    user = db.query(User).filter(
        User.email == data.email,
        User.is_active.is_(True)
    ).first()

    if user:
        now = utc_now()

        # Only the newest link stays valid
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None)
        ).update({"used_at": now})

        raw_token, token_hash = generate_reset_token()

        db.add(PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=config.RESET_TOKEN_MINUTES)
        ))
        db.commit()

        reset_link = f"{config.FRONTEND_URL}/reset-password?token={raw_token}"
        background_tasks.add_task(
            send_password_reset_email, user.email, user.name, reset_link
        )

    return {"message": FORGOT_PASSWORD_MESSAGE}


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(
    data: ResetPasswordRequest,
    db: Session = Depends(get_db)
):
    now = utc_now()

    reset_token = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == hash_reset_token(data.token)
    ).first()

    if (
        not reset_token
        or reset_token.used_at is not None
        or reset_token.expires_at < now
        or not reset_token.user.is_active
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired."
        )

    user = reset_token.user
    user.hashed_password = hash_password(data.new_password)

    # Sign out every existing session for this user
    user.token_version += 1

    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None)
    ).update({"used_at": now})

    db.commit()
    email_login_limiter.reset(user.email)

    return {"message": "Your password has been reset. You can now sign in."}


# ---------------- USER MANAGEMENT APIs (admin only) ----------------

@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED
)
def create_user(
    data: UserCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    existing = db.query(User).filter(User.email == data.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists."
        )

    user = User(
        name=data.name,
        email=data.email,
        hashed_password=hash_password(data.password),
        role=data.role,
        is_active=data.is_active,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info("Admin %s created user %s", admin.id, user.id)
    return user


@router.get("/users", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    return db.query(User).order_by(User.id).all()


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    data: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    update_data = data.model_dump(exclude_unset=True)

    # Fields that cannot be cleared
    for field in ("name", "email", "role", "is_active"):
        if field in update_data and update_data[field] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{field} cannot be empty"
            )

    # Admins cannot lock themselves out
    if user.id == admin.id and (
        update_data.get("role", "admin") != "admin"
        or update_data.get("is_active", True) is False
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "You cannot remove your own admin access "
                "or deactivate your own account."
            )
        )

    new_email = update_data.get("email")
    if new_email and new_email != user.email:
        existing = db.query(User).filter(User.email == new_email).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email already exists."
            )

    password = update_data.pop("password", None)
    if password:
        user.hashed_password = hash_password(password)
        user.token_version += 1

    for field, value in update_data.items():
        setattr(user, field, value)

    db.commit()
    db.refresh(user)

    logger.info("Admin %s updated user %s", admin.id, user.id)
    return user
