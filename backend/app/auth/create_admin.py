"""Create the first VIGIL administrator account.

Run from the backend directory:

    ..\\.venv\\Scripts\\python -m app.auth.create_admin --name "Jane Doe" --email jane@company.com

The password is asked for interactively. For unattended setup it can be
supplied through the VIGIL_ADMIN_PASSWORD environment variable instead.

The command only works while no administrator exists. Further admins
must be created by an existing admin from the Users page or API.
"""

import argparse
import getpass
import os
import sys

from pydantic import ValidationError

from app.auth.models import User
from app.auth.schemas import UserCreate
from app.auth.security import hash_password
from app.db import SessionLocal, init_db


def read_password() -> str:
    password = os.getenv("VIGIL_ADMIN_PASSWORD")
    if password:
        return password

    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        sys.exit("Passwords do not match.")

    return password


def main():
    parser = argparse.ArgumentParser(
        description="Create the first VIGIL administrator account."
    )
    parser.add_argument("--name", default=os.getenv("VIGIL_ADMIN_NAME"))
    parser.add_argument("--email", default=os.getenv("VIGIL_ADMIN_EMAIL"))
    args = parser.parse_args()

    if not args.name or not args.email:
        parser.error(
            "--name and --email are required "
            "(or set VIGIL_ADMIN_NAME and VIGIL_ADMIN_EMAIL)"
        )

    init_db()
    db = SessionLocal()

    try:
        if db.query(User).filter(User.role == "admin").first():
            sys.exit(
                "An administrator already exists. Sign in as an admin "
                "and create additional users from the Users page."
            )

        try:
            data = UserCreate(
                name=args.name,
                email=args.email,
                password=read_password(),
                role="admin",
            )
        except ValidationError as error:
            messages = [item["msg"] for item in error.errors()]
            sys.exit("Invalid input: " + "; ".join(messages))

        if db.query(User).filter(User.email == data.email).first():
            sys.exit("A user with this email already exists.")

        admin = User(
            name=data.name,
            email=data.email,
            hashed_password=hash_password(data.password),
            role="admin",
            is_active=True,
        )

        db.add(admin)
        db.commit()

        print(f"Administrator {admin.email} created. You can now sign in.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
