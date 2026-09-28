import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt
from pwdlib import PasswordHash

from app.core.config import settings
from app.core.exceptions import AuthenticationError

password_hash = PasswordHash.recommended()  # argon2id


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_token(subject: int, token_type: TokenType, ttl: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(subject),
        "type": token_type.value,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int) -> str:
    return create_token(
        user_id, TokenType.ACCESS, timedelta(minutes=settings.access_token_expire_minutes)
    )


def create_refresh_token(user_id: int) -> str:
    return create_token(
        user_id, TokenType.REFRESH, timedelta(days=settings.refresh_token_expire_days)
    )


def decode_token(token: str, expected_type: TokenType) -> int:
    """Returns user id from a valid token of the expected type."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "type", "exp"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired", code="TOKEN_EXPIRED") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid token", code="INVALID_TOKEN") from exc

    # Refresh token must not be usable as access token and vice versa.
    if payload["type"] != expected_type.value:
        raise AuthenticationError("Invalid token type", code="INVALID_TOKEN")
    try:
        return int(payload["sub"])
    except ValueError as exc:
        raise AuthenticationError("Invalid token", code="INVALID_TOKEN") from exc
