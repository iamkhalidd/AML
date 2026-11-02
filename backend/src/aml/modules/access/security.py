"""Credential primitives (ADR-005): Argon2id passwords, TOTP second factor, session tokens."""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime
from urllib.parse import quote, urlencode

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet

MIN_PASSWORD_LENGTH = 12
TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
TOTP_ISSUER = "AML Platform"

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters
# Verified against when the email is unknown, so the response takes as long either way.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Passwords need at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except VerificationError, InvalidHashError:
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_totp_secret() -> bytes:
    return secrets.token_bytes(20)


def totp_uri(secret: bytes, account: str) -> str:
    """otpauth:// URI for authenticator apps (shown once, at enrolment)."""
    params = urlencode(
        {
            "secret": base64.b32encode(secret).decode().rstrip("="),
            "issuer": TOTP_ISSUER,
            "digits": TOTP_DIGITS,
            "period": TOTP_PERIOD_SECONDS,
        }
    )
    return f"otpauth://totp/{quote(TOTP_ISSUER)}:{quote(account)}?{params}"


def totp_step(at: datetime) -> int:
    return int(at.timestamp()) // TOTP_PERIOD_SECONDS


def totp_code(secret: bytes, step: int) -> str:
    """RFC 6238 code (HMAC-SHA1, as every common authenticator app expects)."""
    mac = hmac.new(secret, step.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = int.from_bytes(mac[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(value % 10**TOTP_DIGITS).zfill(TOTP_DIGITS)


def verify_totp(secret: bytes, code: str, at: datetime, last_step: int | None) -> int | None:
    """Return the matching time step, allowing one step of clock drift, or None.

    A step at or before ``last_step`` is refused, so each code works only once.
    """
    current = totp_step(at)
    for step in (current - 1, current, current + 1):
        if (last_step is None or step > last_step) and hmac.compare_digest(
            totp_code(secret, step), code
        ):
            return step
    return None


class SecretCipher:
    """Encrypts TOTP secrets at rest with the application MFA key."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, secret: bytes) -> bytes:
        return self._fernet.encrypt(secret)

    def decrypt(self, token: bytes) -> bytes:
        return self._fernet.decrypt(token)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def csrf_token(session_token: str) -> str:
    """CSRF token bound to the session: derived from it, so nothing extra is stored."""
    return hmac.new(session_token.encode(), b"csrf", hashlib.sha256).hexdigest()
