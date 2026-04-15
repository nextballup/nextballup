from __future__ import annotations

import secrets

# Crockford-style alphanumeric (no 0/O/1/I/L) — easy to dictate over the phone
# without ambiguity. 32^10 ≈ 1.1e15 keyspace, well above any realistic invite
# population. The DB uniqueness constraint catches the rare collision.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_DEFAULT_LENGTH = 10


def generate_invite_code(length: int = _DEFAULT_LENGTH) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))
