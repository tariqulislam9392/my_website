from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.commerce import Coupon, CouponType

CENT = Decimal("0.01")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def calculate_coupon(
    db: Session,
    code: str,
    subtotal: Decimal,
) -> tuple[Coupon, Decimal]:
    coupon = db.scalar(
        select(Coupon).where(
            Coupon.code == code.strip().upper(),
            Coupon.is_active.is_(True),
        )
    )
    now = datetime.now(UTC)
    if (
        coupon is None
        or (coupon.starts_at is not None and _utc(coupon.starts_at) > now)
        or (coupon.expires_at is not None and _utc(coupon.expires_at) <= now)
        or (coupon.usage_limit is not None and coupon.usage_count >= coupon.usage_limit)
        or subtotal < coupon.minimum_order
    ):
        raise HTTPException(status_code=422, detail="Coupon is invalid or unavailable")

    if coupon.discount_type == CouponType.percentage:
        discount = (subtotal * coupon.discount_value / Decimal("100")).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
    else:
        discount = coupon.discount_value
    if coupon.max_discount is not None:
        discount = min(discount, coupon.max_discount)
    return coupon, min(discount, subtotal).quantize(CENT, rounding=ROUND_HALF_UP)
