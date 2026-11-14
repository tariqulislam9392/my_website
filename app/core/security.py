import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt import InvalidTokenError

from app.core.config import settings

_DEVELOPMENT_SECRET = secrets.token_urlsafe(48)
_HASH_N = 2**14
_HASH_R = 8
_HASH_P = 1
_HASH_LENGTH = 64


def _jwt_secret() -> str:
    if settings.jwt_secret_key is not None:
        return settings.jwt_secret_key.get_secret_value()
    if settings.environment == "production":
        raise RuntimeError("JWT_SECRET_KEY must be configured in production")
    return _DEVELOPMENT_SECRET


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_HASH_N,
        r=_HASH_R,
        p=_HASH_P,
        dklen=_HASH_LENGTH,
    )
    return f"scrypt${_HASH_N}${_HASH_R}${_HASH_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = password_hash.split("$")
        if (
            scheme != "scrypt"
            or int(n) != _HASH_N
            or int(r) != _HASH_R
            or int(p) != _HASH_P
        ):
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=_HASH_N,
            r=_HASH_R,
            p=_HASH_P,
            dklen=_HASH_LENGTH,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def create_access_token(user_id: int, auth_version: int = 0) -> str:
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(
        {
            "sub": str(user_id),
            "ver": auth_version,
            "jti": secrets.token_urlsafe(24),
            "iat": now,
            "exp": expires_at,
        },
        _jwt_secret(),
        algorithm="HS256",
    )


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "jti", "sub"]},
        )
    except InvalidTokenError:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    if not isinstance(claims.get("jti"), str):
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims
