from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.commerce import UserAddress
from app.models.user import User
from app.schemas.auth import ProfileUpdate, UserRead
from app.schemas.commerce import AddressInput, AddressRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
def read_profile(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current_user)


@router.patch("/me", response_model=UserRead)
def update_profile(
    payload: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserRead:
    updates = payload.model_dump(exclude_unset=True)
    if "email" in updates:
        duplicate = db.scalar(
            select(User).where(
                User.email == str(updates["email"]),
                User.id != current_user.id,
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists",
            )
        updates["email"] = str(updates["email"])

    for field, value in updates.items():
        setattr(current_user, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        ) from None

    db.refresh(current_user)
    return UserRead.model_validate(current_user)


@router.get("/me/addresses", response_model=list[AddressRead])
def list_addresses(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[UserAddress]:
    return list(
        db.scalars(
            select(UserAddress)
            .where(UserAddress.user_id == current_user.id)
            .order_by(UserAddress.is_default.desc(), UserAddress.id)
        )
    )


@router.post(
    "/me/addresses",
    response_model=AddressRead,
    status_code=status.HTTP_201_CREATED,
)
def create_address(
    payload: AddressInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserAddress:
    if payload.is_default:
        db.query(UserAddress).filter(
            UserAddress.user_id == current_user.id
        ).update({UserAddress.is_default: False})
    address = UserAddress(user_id=current_user.id, **payload.model_dump())
    db.add(address)
    db.commit()
    db.refresh(address)
    return address


@router.put("/me/addresses/{address_id}", response_model=AddressRead)
def update_address(
    address_id: int,
    payload: AddressInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserAddress:
    address = db.scalar(
        select(UserAddress).where(
            UserAddress.id == address_id,
            UserAddress.user_id == current_user.id,
        )
    )
    if address is None:
        raise HTTPException(status_code=404, detail="Address not found")
    if payload.is_default:
        db.query(UserAddress).filter(
            UserAddress.user_id == current_user.id,
            UserAddress.id != address.id,
        ).update({UserAddress.is_default: False})
    for field, value in payload.model_dump().items():
        setattr(address, field, value)
    db.commit()
    db.refresh(address)
    return address


@router.delete("/me/addresses/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_address(
    address_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    address = db.scalar(
        select(UserAddress).where(
            UserAddress.id == address_id,
            UserAddress.user_id == current_user.id,
        )
    )
    if address is None:
        raise HTTPException(status_code=404, detail="Address not found")
    db.delete(address)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    current_user.is_active = False
    current_user.auth_version += 1
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
