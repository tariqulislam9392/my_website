import hashlib
import logging
import secrets
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_token_claims
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models.commerce import PasswordResetToken
from app.models.user import RevokedToken, User, UserRole
from app.schemas.auth import LoginRequest, TokenResponse, UserCreate, UserRead
from app.schemas.commerce import (
    GenericMessage,
    PasswordResetConfirm,
    PasswordResetRequest,
)

router = APIRouter(prefix="/auth", tags=["authentication"])
logger = logging.getLogger(__name__)


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> TokenResponse:
    user = User(
        name=payload.name,
        email=str(payload.email),
        password_hash=hash_password(payload.password),
        role=UserRole.customer,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        ) from None

    db.refresh(user)
    return TokenResponse(
        access_token=create_access_token(user.id, user.auth_version),
        user=UserRead.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == str(payload.email)))
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenResponse(
        access_token=create_access_token(user.id, user.auth_version),
        user=UserRead.model_validate(user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    claims: dict[str, int | str] = Depends(get_token_claims),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    db.add(
        RevokedToken(
            jti=str(claims["jti"]),
            user_id=current_user.id,
            expires_at=datetime.fromtimestamp(int(claims["exp"]), UTC),
        )
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/forgot-password", response_model=GenericMessage, status_code=202)
def forgot_password(
    payload: PasswordResetRequest,
    db: Session = Depends(get_db),
) -> GenericMessage:
    email = payload.email.strip().lower()
    user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    response = GenericMessage(
        message="If the account exists, password reset instructions will be sent."
    )
    if user is None:
        return response

    raw_token = secrets.token_urlsafe(32)
    token_record = PasswordResetToken(
        user_id=user.id,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(minutes=20),
    )
    db.add(token_record)
    db.commit()

    if settings.environment in {"development", "test"}:
        response.reset_token = raw_token
        return response
    if not all(
        [
            settings.smtp_host,
            settings.smtp_username,
            settings.smtp_password,
            settings.smtp_sender,
        ]
    ):
        logger.error("Password reset email requested but SMTP is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password reset email service is not configured",
        )

    message = EmailMessage()
    message["Subject"] = "ShopHub password reset"
    message["From"] = settings.smtp_sender
    message["To"] = user.email
    message.set_content(
        f"Use this password reset token within 20 minutes: {raw_token}"
    )
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(
                settings.smtp_username,
                settings.smtp_password.get_secret_value(),
            )
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException):
        logger.exception("Could not send ShopHub password reset email")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not send password reset email",
        ) from None
    return response


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    payload: PasswordResetConfirm,
    db: Session = Depends(get_db),
) -> Response:
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    record = db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > datetime.now(UTC),
        )
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )
    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    now = datetime.now(UTC)
    record.used_at = now
    user.password_hash = hash_password(payload.new_password)
    user.auth_version += 1
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
