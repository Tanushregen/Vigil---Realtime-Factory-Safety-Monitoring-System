import os

from cryptography.fernet import Fernet
from dotenv import load_dotenv


# Load environment variables from .env
BASE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)

load_dotenv(os.path.join(BASE_DIR, ".env"))


# Read encryption key
FERNET_KEY = os.getenv("FERNET_KEY")

if not FERNET_KEY:
    raise ValueError("FERNET_KEY is missing in .env file")


# Create Fernet encryption object
fernet = Fernet(FERNET_KEY.encode())


def encrypt_password(password: str) -> str:
    """Encrypt DVR password before storing it."""
    encrypted_password = fernet.encrypt(password.encode())
    return encrypted_password.decode()


def decrypt_password(encrypted_password: str) -> str:
    """Decrypt DVR password when required by the backend."""
    decrypted_password = fernet.decrypt(
        encrypted_password.encode()
    )
    return decrypted_password.decode()