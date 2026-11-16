from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.permissions import require_roles
from app.core.database import get_db
from app.models.commerce import (
    ApplicationStatus,
    Category,
    Coupon,
    InventoryMovement,
    OrderItem,
    OrderStatus,
    OrderStatusEvent,
    Payment,
    PaymentStatus,
    Product,
    Refund,
    SellerApplication,
    Shop,
    ShopOrder,
)
from app.models.user import User, UserRole
from app.schemas.commerce import (
    AdminProductCreate,
    AdminShopCreate,
    AdminDashboard,
    AdminUserRead,
    CouponCreate,
    CouponRead,
    CouponUpdate,
    OrderRead,
    OrderStatusUpdate,
    PaymentRead,
    PaymentVerification,
    ProductRead,
    RefundCreate,
    RefundRead,
    SellerApplicationRead,
    SellerDecision,
    ShopRead,
    UserActiveUpdate,
)
from app.api.routes.orders import _order_response

router = APIRouter(prefix="/admin", tags=["admin"])
admin_only = require_roles(UserRole.admin)


@router.get("/dashboard", response_model=AdminDashboard)
def dashboard(
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> AdminDashboard:
    refund_totals = (
        select(Refund.payment_id, func.sum(Refund.amount).label("amount"))
        .group_by(Refund.payment_id)
        .subquery()
    )
    total_sales = db.scalar(
        select(
            func.sum(
                Payment.amount - func.coalesce(refund_totals.c.amount, Decimal("0.00"))
            )
        )
        .join(ShopOrder, ShopOrder.id == Payment.order_id)
        .outerjoin(refund_totals, refund_totals.c.payment_id == Payment.id)
        .where(
            ShopOrder.status == OrderStatus.delivered,
            Payment.status.in_(
                [
                    PaymentStatus.paid,
                    PaymentStatus.partially_refunded,
                    PaymentStatus.refunded,
                ]
            ),
        )
    )
    return AdminDashboard(
        total_users=db.scalar(select(func.count(User.id))) or 0,
        total_sellers=db.scalar(
            select(func.count(User.id)).where(User.role == UserRole.seller)
        )
        or 0,
        total_products=db.scalar(select(func.count(Product.id))) or 0,
        total_orders=db.scalar(select(func.count(ShopOrder.id))) or 0,
        total_sales=total_sales or Decimal("0.00"),
        pending_seller_applications=db.scalar(
            select(func.count(SellerApplication.id)).where(
                SellerApplication.status == ApplicationStatus.pending
            )
        )
        or 0,
        pending_orders=db.scalar(
            select(func.count(ShopOrder.id)).where(
                ShopOrder.status == OrderStatus.pending
            )
        )
        or 0,
    )


@router.get("/users", response_model=list[AdminUserRead])
def list_users(
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[User]:
    return list(
        db.scalars(
            select(User).order_by(User.created_at.desc()).offset(offset).limit(limit)
        )
    )


@router.patch("/users/{user_id}", response_model=AdminUserRead)
def update_user_status(
    user_id: int,
    payload: UserActiveUpdate,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == admin.id and not payload.is_active:
        raise HTTPException(status_code=409, detail="Admins cannot deactivate themselves")
    target.is_active = payload.is_active
    if not payload.is_active:
        target.auth_version += 1
    db.commit()
    db.refresh(target)
    return target


@router.get("/seller-applications", response_model=list[SellerApplicationRead])
def list_seller_applications(
    status_filter: ApplicationStatus | None = None,
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> list[SellerApplication]:
    statement = select(SellerApplication).order_by(SellerApplication.submitted_at)
    if status_filter is not None:
        statement = statement.where(SellerApplication.status == status_filter)
    return list(db.scalars(statement))


@router.post(
    "/seller-applications/{application_id}/approve",
    response_model=SellerApplicationRead,
)
def approve_seller(
    application_id: int,
    payload: SellerDecision,
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> SellerApplication:
    application = db.get(SellerApplication, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Seller application not found")
    if application.status != ApplicationStatus.pending:
        raise HTTPException(status_code=409, detail="Application is not pending")
    user = db.get(User, application.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=409, detail="Applicant account is unavailable")
    existing_shop = db.scalar(
        select(Shop).where(func.lower(Shop.name) == application.shop_name.lower())
    )
    if existing_shop is not None:
        raise HTTPException(status_code=409, detail="Shop name is already in use")
    application.status = ApplicationStatus.approved
    application.decided_at = datetime.now(UTC)
    application.decision_note = payload.note
    user.role = UserRole.seller
    db.add(
        Shop(
            owner_id=user.id,
            name=application.shop_name,
            description=application.description,
            is_verified=True,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Seller could not be approved") from None
    db.refresh(application)
    return application


@router.post(
    "/seller-applications/{application_id}/reject",
    response_model=SellerApplicationRead,
)
def reject_seller(
    application_id: int,
    payload: SellerDecision,
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> SellerApplication:
    application = db.get(SellerApplication, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Seller application not found")
    if application.status != ApplicationStatus.pending:
        raise HTTPException(status_code=409, detail="Application is not pending")
    application.status = ApplicationStatus.rejected
    application.decided_at = datetime.now(UTC)
    application.decision_note = payload.note
    db.commit()
    db.refresh(application)
    return application


@router.get("/shops", response_model=list[ShopRead])
def admin_list_shops(
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> list[Shop]:
    return list(db.scalars(select(Shop).order_by(Shop.created_at.desc())))


@router.post("/shops", response_model=ShopRead, status_code=status.HTTP_201_CREATED)
def create_admin_shop(
    payload: AdminShopCreate,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> Shop:
    existing_shop = db.scalar(select(Shop).where(Shop.owner_id == admin.id))
    if existing_shop is not None:
        raise HTTPException(status_code=409, detail="Admin shop already exists")
    shop = Shop(
        owner_id=admin.id,
        name=payload.name,
        description=payload.description,
        is_verified=True,
    )
    db.add(shop)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Shop name is already in use") from None
    db.refresh(shop)
    return shop


@router.get("/products", response_model=list[ProductRead])
def admin_list_products(
    shop_id: int | None = Query(default=None, gt=0),
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> list[Product]:
    statement = select(Product).order_by(Product.created_at.desc())
    if shop_id is not None:
        statement = statement.where(Product.shop_id == shop_id)
    return list(db.scalars(statement))


@router.post("/products", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def admin_create_product(
    payload: AdminProductCreate,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> Product:
    shop = db.get(Shop, payload.shop_id)
    if shop is None or shop.owner_id != admin.id or not shop.is_active:
        raise HTTPException(status_code=404, detail="Active admin shop not found")
    category = db.get(Category, payload.category_id)
    if category is None or not category.is_active:
        raise HTTPException(status_code=404, detail="Active category not found")
    product_data = payload.model_dump(exclude={"shop_id"})
    product = Product(shop_id=shop.id, **product_data)
    db.add(product)
    db.flush()
    if product.stock:
        db.add(
            InventoryMovement(
                product_id=product.id,
                actor_id=admin.id,
                quantity_change=product.stock,
                reason="Initial stock",
            )
        )
    db.commit()
    db.refresh(product)
    return product


@router.patch("/shops/{shop_id}/active")
def set_shop_active(
    shop_id: int,
    is_active: bool,
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    shop = db.get(Shop, shop_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    shop.is_active = is_active
    db.commit()
    return {"is_active": shop.is_active}


@router.post("/coupons", response_model=CouponRead, status_code=201)
def create_coupon(
    payload: CouponCreate,
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> Coupon:
    coupon = Coupon(
        **{
            **payload.model_dump(),
            "code": payload.code.strip().upper(),
        }
    )
    db.add(coupon)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Coupon code already exists") from None
    db.refresh(coupon)
    return coupon


@router.get("/coupons", response_model=list[CouponRead])
def list_coupons(
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> list[Coupon]:
    return list(db.scalars(select(Coupon).order_by(Coupon.id.desc())))


@router.patch("/coupons/{coupon_id}", response_model=CouponRead)
def update_coupon(
    coupon_id: int,
    payload: CouponUpdate,
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> Coupon:
    coupon = db.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=404, detail="Coupon not found")
    coupon.is_active = payload.is_active
    db.commit()
    db.refresh(coupon)
    return coupon


@router.get("/orders", response_model=list[OrderRead])
def admin_list_orders(
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
    status_filter: OrderStatus | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[OrderRead]:
    statement = select(ShopOrder).order_by(ShopOrder.created_at.desc())
    if status_filter is not None:
        statement = statement.where(ShopOrder.status == status_filter)
    orders = list(db.scalars(statement.offset(offset).limit(limit)))
    return [_order_response(db, order) for order in orders]


@router.patch("/orders/{order_id}/status", response_model=OrderRead)
def update_order_status(
    order_id: int,
    payload: OrderStatusUpdate,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> OrderRead:
    order = db.get(ShopOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    transitions = {
        OrderStatus.pending: {OrderStatus.confirmed, OrderStatus.cancelled},
        OrderStatus.confirmed: {OrderStatus.shipped, OrderStatus.cancelled},
        OrderStatus.shipped: {OrderStatus.delivered},
    }
    if payload.status not in transitions.get(order.status, set()):
        raise HTTPException(status_code=409, detail="Invalid order status transition")
    if payload.status == OrderStatus.cancelled:
        payment = db.scalar(select(Payment).where(Payment.order_id == order.id))
        if payment is not None and payment.status in {
            PaymentStatus.paid,
            PaymentStatus.partially_refunded,
        }:
            raise HTTPException(
                status_code=409,
                detail="Paid order cancellation requires a refund first",
            )
        for item in db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)):
            if item.product_id is None:
                continue
            db.execute(
                update(Product)
                .where(Product.id == item.product_id)
                .values(stock=Product.stock + item.quantity)
            )
            db.add(
                InventoryMovement(
                    product_id=item.product_id,
                    actor_id=admin.id,
                    quantity_change=item.quantity,
                    reason=f"Cancelled order #{order.id}",
                )
            )
        if order.coupon_code is not None:
            db.execute(
                update(Coupon)
                .where(
                    Coupon.code == order.coupon_code,
                    Coupon.usage_count > 0,
                )
                .values(usage_count=Coupon.usage_count - 1)
            )
    if payload.tracking_number is not None:
        if payload.status != OrderStatus.shipped:
            raise HTTPException(
                status_code=422,
                detail="Tracking number can only be set when shipping an order",
            )
        order.tracking_number = payload.tracking_number
    order.status = payload.status
    db.add(
        OrderStatusEvent(
            order_id=order.id,
            actor_id=admin.id,
            status=payload.status,
            note=payload.note,
        )
    )
    db.commit()
    db.refresh(order)
    return _order_response(db, order)


@router.get("/payments", response_model=list[PaymentRead])
def admin_list_payments(
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[Payment]:
    return list(
        db.scalars(
            select(Payment)
            .order_by(Payment.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )


@router.post("/payments/{payment_id}/verify", response_model=PaymentRead)
def verify_payment(
    payment_id: int,
    payload: PaymentVerification,
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status != PaymentStatus.pending:
        raise HTTPException(status_code=409, detail="Payment is not pending")
    payment.status = PaymentStatus.paid
    payment.provider_reference = payload.provider_reference
    db.commit()
    db.refresh(payment)
    return payment


@router.post(
    "/payments/{payment_id}/refund",
    response_model=RefundRead,
    status_code=status.HTTP_201_CREATED,
)
def issue_refund(
    payment_id: int,
    payload: RefundCreate,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> Refund:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status not in {PaymentStatus.paid, PaymentStatus.partially_refunded}:
        raise HTTPException(status_code=409, detail="Payment is not refundable")
    already_refunded = db.scalar(
        select(func.coalesce(func.sum(Refund.amount), 0)).where(
            Refund.payment_id == payment.id
        )
    ) or Decimal("0.00")
    if payload.amount > payment.amount - already_refunded:
        raise HTTPException(
            status_code=422,
            detail="Refund amount exceeds the remaining paid amount",
        )
    refund = Refund(
        payment_id=payment.id,
        admin_id=admin.id,
        amount=payload.amount,
        reason=payload.reason,
    )
    db.add(refund)
    total_refunded = already_refunded + payload.amount
    payment.status = (
        PaymentStatus.refunded
        if total_refunded >= payment.amount
        else PaymentStatus.partially_refunded
    )
    db.commit()
    db.refresh(refund)
    return refund
