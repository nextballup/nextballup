"""TOTP (RFC 6238) helpers + AES-GCM encryption for the shared secret.

The implementation deliberately uses only stdlib + `cryptography` (already a
transitive dep via `bcrypt` and the test fixtures). We do not pull in
`pyotp` because (a) the protocol is small enough to maintain in-house and
(b) every external dependency is one more thing to vet for license and
supply-chain risk.

Threat model:
  * The TOTP shared secret never leaves storage in plaintext. The cipher is
    AES-256-GCM keyed with a per-row nonce; the AES key is derived from
    `MFA_SECRET_KEY` via PBKDF2-HMAC-SHA256. A future hardening pass moves
    the key custody to KMS — the on-disk format reserves a `cipher` column
    so we can introduce a new format and migrate row-by-row.
  * Verification accepts the current TOTP step ± `_VERIFY_WINDOW` steps to
    tolerate small clock drift, but rejects replays of the most recently
    used step via `last_used_at` so an attacker cannot reuse a sniffed code.
  * Recovery codes are 10-character base32 strings, stored as SHA-256 hashes
    only. Each is single-use.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Algorithm parameters.
_KEY_BYTES = 32  # AES-256
_NONCE_BYTES = 12
_PBKDF2_ITERATIONS = 200_000
_PBKDF2_SALT = b"nbu-mfa-pbkdf2-salt-v1"  # static; the secret_key carries the entropy
_TOTP_ALG = hashlib.sha1  # RFC 6238 default; widely supported by authenticator apps
_VERIFY_WINDOW = 1  # accept ±1 step (≈30s either side at default 30s step)
_SECRET_BYTES = 20  # 160-bit shared secret recommended for SHA-1


def _derive_key(secret_key: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KEY_BYTES,
        salt=_PBKDF2_SALT,
        iterations=_PBKDF2_ITERATIONS,
    )
    return kdf.derive(secret_key.encode("utf-8"))


def _normalise_b32(secret_b32: str) -> str:
    cleaned = secret_b32.replace(" ", "").replace("-", "").upper()
    # base32 is padding-tolerant; we accept missing pad and add it back.
    pad = (-len(cleaned)) % 8
    return cleaned + ("=" * pad)


def generate_totp_secret() -> str:
    """Return a new base32-encoded shared secret (no padding, lowercase-safe)."""
    raw = secrets.token_bytes(_SECRET_BYTES)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def totp_provisioning_uri(
    *,
    secret_b32: str,
    issuer: str,
    account: str,
    digits: int,
    step_seconds: int,
) -> str:
    """Build the `otpauth://totp/...` URI compatible with Google Authenticator,
    1Password, etc. We URL-encode the labels minimally — issuer / account
    are user-controlled so they can carry spaces and most punctuation but
    not `?`, `&`, `:` which we strip.
    """
    safe_issuer = "".join(c for c in issuer if c not in "?&:")
    safe_account = "".join(c for c in account if c not in "?&:")
    label = f"{safe_issuer}:{safe_account}"
    params = (
        f"secret={secret_b32}&issuer={safe_issuer}&algorithm=SHA1"
        f"&digits={digits}&period={step_seconds}"
    )
    return f"otpauth://totp/{label}?{params}"


def encrypt_secret(plaintext: str, *, master_key: str) -> str:
    """Encrypt a base32 TOTP secret. Returns `nonce_b64.ciphertext_b64`."""
    aes_key = _derive_key(master_key)
    aes = AESGCM(aes_key)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), associated_data=b"nbu-mfa-totp")
    return f"{base64.urlsafe_b64encode(nonce).decode('ascii')}.{base64.urlsafe_b64encode(ct).decode('ascii')}"


def decrypt_secret(blob: str, *, master_key: str) -> str:
    if blob.count(".") != 1:
        raise ValueError("Malformed ciphertext blob")
    nonce_b64, ct_b64 = blob.split(".")
    nonce = base64.urlsafe_b64decode(nonce_b64)
    ct = base64.urlsafe_b64decode(ct_b64)
    aes = AESGCM(_derive_key(master_key))
    plaintext = aes.decrypt(nonce, ct, associated_data=b"nbu-mfa-totp")
    return plaintext.decode("utf-8")


def _hotp(*, key: bytes, counter: int, digits: int) -> str:
    counter_bytes = struct.pack(">Q", counter)
    digest = hmac.new(key, counter_bytes, _TOTP_ALG).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10**digits)).zfill(digits)


def totp_now(*, secret_b32: str, step_seconds: int, digits: int, at: int | None = None) -> str:
    """Helper for tests and emergency code generation. Production verify
    uses `verify_totp_code` which iterates the validation window."""
    counter = (at if at is not None else int(time.time())) // step_seconds
    key = base64.b32decode(_normalise_b32(secret_b32))
    return _hotp(key=key, counter=counter, digits=digits)


@dataclass(frozen=True)
class TotpVerification:
    accepted: bool
    matched_counter: int | None


def verify_totp_code(
    *,
    secret_b32: str,
    code: str,
    step_seconds: int,
    digits: int,
    at: int | None = None,
    last_used_counter: int | None = None,
) -> TotpVerification:
    """Constant-time check across the validation window.

    `last_used_counter` provides per-secret replay protection: a freshly
    successful match returns its counter, which the caller persists to
    `last_used_at` (mapped to the counter via `step_seconds`). A subsequent
    submission with the same counter is rejected.
    """
    if not code or not code.isdigit() or len(code) != digits:
        return TotpVerification(accepted=False, matched_counter=None)
    key = base64.b32decode(_normalise_b32(secret_b32))
    base_counter = (at if at is not None else int(time.time())) // step_seconds
    expected_codes: list[tuple[int, str]] = []
    for delta in range(-_VERIFY_WINDOW, _VERIFY_WINDOW + 1):
        counter = base_counter + delta
        expected_codes.append((counter, _hotp(key=key, counter=counter, digits=digits)))
    matched_counter: int | None = None
    accepted = False
    for counter, expected in expected_codes:
        if hmac.compare_digest(expected, code):
            if last_used_counter is not None and counter <= last_used_counter:
                # Replay: the same (or older) counter has already been used.
                return TotpVerification(accepted=False, matched_counter=None)
            matched_counter = counter
            accepted = True
            break
    return TotpVerification(accepted=accepted, matched_counter=matched_counter)


# ---------- Recovery codes ----------------------------------------------------

_RECOVERY_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # Crockford-ish, no ambiguous glyphs
_RECOVERY_LEN = 10


def generate_recovery_codes(count: int) -> list[str]:
    """Generate `count` plaintext recovery codes. The caller hashes for
    storage and shows the plaintext to the user once.
    """
    return [
        "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_LEN))
        for _ in range(count)
    ]


def hash_recovery_code(code: str, *, pepper: str | None = None) -> str:
    """Hash a recovery code after normalising user-entered formatting.

    Application code passes the MFA master key as a pepper so a read-only DB
    compromise cannot brute-force the short recovery-code space offline.
    Tests may omit it when only normalisation behavior is under inspection.
    """
    normalized = code.replace(" ", "").upper().encode("utf-8")
    if pepper:
        return hmac.new(pepper.encode("utf-8"), normalized, hashlib.sha256).hexdigest()
    return hashlib.sha256(normalized).hexdigest()
