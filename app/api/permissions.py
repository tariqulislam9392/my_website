from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.commerce import Shop
from app.models.user import User, UserRole


def require_roles(*roles: UserRole) -> Callable[..., User]:
    def role_dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return role_dependency


def get_active_shop(
    current_user: User = Depends(require_roles(UserRole.seller)),
    db: Session = Depends(get_db),
) -> Shop:
    shop = db.scalar(
        select(Shop).where(
            Shop.owner_id == current_user.id,
            Shop.is_active.is_(True),
            Shop.is_verified.is_(True),
        )
    )
    if shop is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An approved active shop is required",
        )
    return shop
