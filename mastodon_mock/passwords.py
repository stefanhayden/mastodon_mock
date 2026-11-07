"""Password hashing for ``[auth] require_password`` mode (stdlib scrypt, no extra deps)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

__all__ = ["hash_password", "verify_password"]

_N, _R, _P = 2**14, 8, 1
_DKLEN = 32


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    """Hash ``password`` with a fresh random salt.

    Args:
        password: The plaintext password.

    Returns:
        A self-describing ``scrypt$N$r$p$salt$hash`` string.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    """Check ``password`` against a hash from ``hash_password``.

    Args:
        password: The plaintext password to check.
        stored: The stored hash, or ``None`` for an account with no password.

    Returns:
        Whether the password matches. Accounts with no password never match.
    """
    if not stored:
        return False
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)
