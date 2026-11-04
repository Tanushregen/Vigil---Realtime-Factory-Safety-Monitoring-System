import os

from dotenv import load_dotenv


# Load environment variables from the project-root .env
BASE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)

load_dotenv(os.path.join(BASE_DIR, ".env"))


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


# JWT signing secret (required)
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")

if len(JWT_SECRET_KEY) < 32:
    raise ValueError(
        "JWT_SECRET_KEY is missing or shorter than 32 characters in .env file"
    )

JWT_ALGORITHM = "HS256"

# Session length without and with "Remember me"
SESSION_HOURS = env_int("SESSION_HOURS", 8)
REMEMBER_ME_DAYS = env_int("REMEMBER_ME_DAYS", 7)

# Cookie settings. Set COOKIE_SECURE=true when serving over HTTPS.
COOKIE_NAME = "vigil_session"
COOKIE_SECURE = env_bool("COOKIE_SECURE", False)
COOKIE_SAMESITE = "lax"

# Browser origins allowed to make state-changing requests
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

# Login-attempt protection
LOGIN_MAX_FAILURES_PER_EMAIL = env_int("LOGIN_MAX_FAILURES_PER_EMAIL", 5)
LOGIN_MAX_FAILURES_PER_IP = env_int("LOGIN_MAX_FAILURES_PER_IP", 30)
LOGIN_LOCKOUT_MINUTES = env_int("LOGIN_LOCKOUT_MINUTES", 15)

# Password reset
RESET_TOKEN_MINUTES = env_int("RESET_TOKEN_MINUTES", 30)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")

# SMTP settings. Password reset by email is enabled only when these are set.
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = env_int("SMTP_PORT", 587)
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "").strip()
SMTP_USE_TLS = env_bool("SMTP_USE_TLS", True)

EMAIL_ENABLED = bool(SMTP_HOST and SMTP_FROM)
