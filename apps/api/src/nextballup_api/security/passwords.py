from __future__ import annotations

import bcrypt

# bcrypt ignores bytes beyond 72. Rejecting overlong passwords avoids creating
# prefix-equivalent secrets that appear distinct to users.
_BCRYPT_MAX_BYTES = 72


def _encode(password: str) -> bytes:
    encoded = password.encode("utf-8")
    if len(encoded) > _BCRYPT_MAX_BYTES:
        raise ValueError(
            f"Password must be {_BCRYPT_MAX_BYTES} UTF-8 bytes or fewer for bcrypt compatibility"
        )
    return encoded


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_encode(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_encode(password), password_hash.encode("utf-8"))
    except ValueError:
        return False
