from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

Role = Literal["admin", "user"]


def validate_new_password(value):
    if value.strip() != value:
        raise ValueError("Password cannot start or end with spaces")
    if not any(char.isalpha() for char in value) or not any(
        char.isdigit() for char in value
    ):
        raise ValueError("Password must contain letters and numbers")
    return value


def normalize_email(value):
    return value.strip().lower()


def validate_person_name(value):
    if not value or not value.strip():
        raise ValueError("Name cannot be empty")
    return value.strip()


class LoginRequest(BaseModel):
    email: EmailStr
    # No strength rules here, only a length cap so huge inputs are rejected
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    remember_me: bool = False

    @field_validator("email")
    @classmethod
    def check_email(cls, value):
        return normalize_email(value)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    role: Role
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_login: Optional[datetime] = None


class LoginResponse(BaseModel):
    user: UserResponse
    expires_at: datetime


class AuthConfigResponse(BaseModel):
    password_reset_enabled: bool


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def check_email(cls, value):
        return normalize_email(value)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH
    )

    @field_validator("new_password")
    @classmethod
    def check_password(cls, value):
        return validate_new_password(value)


class MessageResponse(BaseModel):
    message: str


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH
    )
    role: Role = "user"
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def check_name(cls, value):
        return validate_person_name(value)

    @field_validator("email")
    @classmethod
    def check_email(cls, value):
        return normalize_email(value)

    @field_validator("password")
    @classmethod
    def check_password(cls, value):
        return validate_new_password(value)


class UserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    email: Optional[EmailStr] = None
    role: Optional[Role] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(
        default=None,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH
    )

    @field_validator("name")
    @classmethod
    def check_name(cls, value):
        if value is not None:
            return validate_person_name(value)
        return value

    @field_validator("email")
    @classmethod
    def check_email(cls, value):
        if value is not None:
            return normalize_email(value)
        return value

    @field_validator("password")
    @classmethod
    def check_password(cls, value):
        if value is not None:
            return validate_new_password(value)
        return value
