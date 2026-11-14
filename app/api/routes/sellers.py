from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.permissions import get_active_shop, require_roles
from app.core.database import get_db
from app.models.commerce import (
    ApplicationStatus,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentStatus,
    Product,
    SellerApplication,
    Shop,
    ShopOrder,
)
from app.models.user import User, UserRole
from app.schemas.commerce import (
    SellerApplicationCreate,
    SellerApplicationRead,
    SellerDashboard,
    ShopRead,
    ShopUpdate,
)

router = APIRouter(tags=["sellers"])


@router.post(
    "/sellers/apply",
    response_model=SellerApplicationRead,
    status_code=status.HTTP_201_CREATED,
)
def apply_to_sell(
    payload: SellerApplicationCreate,
    current_user: User = Depends(require_roles(UserRole.customer)),
    db: Session = Depends(get_db),
) -> SellerApplication:
    shop_name = payload.shop_name.strip()
    existing_name = db.scalar(
        select(Shop).where(func.lower(Shop.name) == shop_name.lower())
    )
    if existing_name is not None:
        raise HTTPException(status_code=409, detail="Shop name is already in use")

    application = db.scalar(
        select(SellerApplication).where(
            SellerApplication.user_id == current_user.id
        )
    )
    if application is not None:
        if application.status != ApplicationStatus.rejected:
            raise HTTPException(
                status_code=409,
                detail="A seller application has already been submitted",
            )
        application.shop_name = shop_name
        application.description = payload.description
        application.status = ApplicationStatus.pending
        application.submitted_at = datetime.now(UTC)
        application.decided_at = None
        application.decision_note = None
    else:
        application = SellerApplication(
            user_id=current_user.id,
            shop_name=shop_name,
            description=payload.description,
        )
        db.add(application)
    db.commit()
    db.refresh(application)
    return application


@router.get("/sellers/application", response_model=SellerApplicationRead)
def read_seller_application(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SellerApplication:
    application = db.scalar(
        select(SellerApplication).where(
            SellerApplication.user_id == current_user.id
        )
    )
    if application is None:
        raise HTTPException(status_code=404, detail="Seller application not found")
    return application


@router.get("/sellers/shop", response_model=ShopRead)
def read_my_shop(shop: Shop = Depends(get_active_shop)) -> Shop:
    return shop


@router.patch("/sellers/shop", response_model=ShopRead)
def update_my_shop(
    payload: ShopUpdate,
    shop: Shop = Depends(get_active_shop),
    db: Session = Depends(get_db),
) -> Shop:
    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates:
        name = updates["name"].strip()
        conflict = db.scalar(
            select(Shop).where(
                func.lower(Shop.name) == name.lower(),
                Shop.id != shop.id,
            )
        )
        if conflict is not None:
            raise HTTPException(status_code=409, detail="Shop name is already in use")
        updates["name"] = name
    for field, value in updates.items():
        setattr(shop, field, value)
    db.commit()
    db.refresh(shop)
    return shop


@router.get("/shops", response_model=list[ShopRead])
def list_shops(
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[Shop]:
    return list(
        db.scalars(
            select(Shop)
            .where(Shop.is_active.is_(True), Shop.is_verified.is_(True))
            .order_by(Shop.name)
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 100))
        )
    )


@router.get("/shops/{shop_id}", response_model=ShopRead)
def read_shop(shop_id: int, db: Session = Depends(get_db)) -> Shop:
    shop = db.scalar(
        select(Shop).where(
            Shop.id == shop_id,
            Shop.is_active.is_(True),
            Shop.is_verified.is_(True),
        )
    )
    if shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    return shop


@router.get("/sellers/dashboard", response_model=SellerDashboard)
def seller_dashboard(
    shop: Shop = Depends(get_active_shop),
    db: Session = Depends(get_db),
) -> SellerDashboard:
    product_count = db.scalar(
        select(func.count(Product.id)).where(Product.shop_id == shop.id)
    ) or 0
    low_stock_count = db.scalar(
        select(func.count(Product.id)).where(
            Product.shop_id == shop.id,
            Product.stock <= Product.low_stock_threshold,
            Product.is_active.is_(True),
        )
    ) or 0
    order_rows = db.execute(
        select(OrderItem.quantity, OrderItem.unit_price)
        .join(ShopOrder, ShopOrder.id == OrderItem.order_id)
        .where(
            OrderItem.shop_id == shop.id,
            ShopOrder.status != OrderStatus.cancelled,
        )
    ).all()
    paid_rows = db.execute(
        select(OrderItem.quantity, OrderItem.unit_price)
        .join(ShopOrder, ShopOrder.id == OrderItem.order_id)
        .join(Payment, Payment.order_id == ShopOrder.id)
        .where(
            OrderItem.shop_id == shop.id,
            ShopOrder.status == OrderStatus.delivered,
            Payment.status.in_(
                [PaymentStatus.paid, PaymentStatus.partially_refunded]
            ),
        )
    ).all()
    return SellerDashboard(
        shop_id=shop.id,
        total_products=product_count,
        total_order_items=sum(row.quantity for row in order_rows),
        total_sales=sum(
            (row.unit_price * row.quantity for row in paid_rows),
            start=Decimal("0.00"),
        ),
        low_stock_products=low_stock_count,
    )
