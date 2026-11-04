import logging
import smtplib
from email.message import EmailMessage

from app.auth import config

logger = logging.getLogger(__name__)


def send_password_reset_email(to_email: str, name: str, reset_link: str):
    """Send the reset link. Runs as a background task after the response."""
    message = EmailMessage()
    message["Subject"] = "Reset your VIGIL password"
    message["From"] = config.SMTP_FROM
    message["To"] = to_email
    message.set_content(
        f"Hello {name},\n\n"
        "A password reset was requested for your VIGIL account.\n"
        f"Open this link to choose a new password:\n\n{reset_link}\n\n"
        f"The link expires in {config.RESET_TOKEN_MINUTES} minutes and "
        "can be used once.\n\n"
        "If you did not request this, you can ignore this email.\n"
    )

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as smtp:
            if config.SMTP_USE_TLS:
                smtp.starttls()
            if config.SMTP_USERNAME:
                smtp.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            smtp.send_message(message)
    except Exception:
        logger.exception("Unable to send password reset email")
