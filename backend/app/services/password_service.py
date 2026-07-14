import re

import bcrypt

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


def validate_registration(username: str, password: str, password_confirmation: str) -> str:
    normalized = username.strip()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError("Username must be 3-64 characters using letters, numbers, dot, dash, or underscore")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    if password != password_confirmation:
        raise ValueError("Password confirmation does not match")
    return normalized


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False
