from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import RevokedToken, User

bearer_scheme = HTTPBearer(auto_error=False)


def get_token_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_access_token(credentials.credentials)


def get_current_user(
    claims: dict[str, Any] = Depends(get_token_claims),
    db: Session = Depends(get_db),
) -> User:
    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    revoked = db.scalar(
        select(RevokedToken).where(RevokedToken.jti == claims["jti"])
    )
    user = db.get(User, user_id)
    if (
        revoked is not None
        or user is None
        or not user.is_active
        or claims.get("ver") != user.auth_version
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
