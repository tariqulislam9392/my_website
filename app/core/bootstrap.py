from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.models.user import User, UserRole


def bootstrap_admin(db: Session) -> None:
    if settings.bootstrap_admin_email is None:
        return
    email = settings.bootstrap_admin_email.strip().lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        if existing.role != UserRole.admin:
            raise RuntimeError(
                "BOOTSTRAP_ADMIN_EMAIL belongs to a non-admin account"
            )
        return
    if settings.bootstrap_admin_password is None:
        raise RuntimeError("BOOTSTRAP_ADMIN_PASSWORD is required")
    db.add(
        User(
            name=settings.bootstrap_admin_name,
            email=email,
            password_hash=hash_password(
                settings.bootstrap_admin_password.get_secret_value()
            ),
            role=UserRole.admin,
        )
    )
    db.commit()
