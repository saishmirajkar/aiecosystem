"""Password hashing and opaque session/reset tokens."""
from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # Argon2id with library defaults

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def validate_password_strength(password: str, email: str | None = None) -> str | None:
    """Return an error message, or None when acceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if len(password) > 256:
        return "Password is too long"
    if email and password.lower() == email.lower():
        return "Password must not equal the email address"
    if password.isdigit() or password.isalpha():
        return "Password must contain a mix of letters and numbers or symbols"
    return None


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# A real hash so login timing does not reveal whether an email exists.
DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")
