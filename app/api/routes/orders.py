from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.commerce import (
    Cart,
    CartItem,
    Category,
    Coupon,
    InventoryMovement,
    OrderItem,
    OrderStatus,
    OrderStatusEvent,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Product,
    ShopOrder,
    Shop,
    UserAddress,
)
from app.models.user import User
from app.schemas.commerce import (
    CouponResult,
    CouponValidate,
    OrderCreate,
    OrderRead,
    OrderStatusEventRead,
    PaymentRead,
    build_order_read,
)
from app.services.coupons import calculate_coupon

router = APIRouter(tags=["orders", "payments"])


def _order_or_404(
    db: Session,
    order_id: int,
    user: User,
) -> ShopOrder:
    order = db.get(ShopOrder, order_id)
    if order is None or (
        user.role.value != "admin" and order.customer_id != user.id
    ):
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _order_response(db: Session, order: ShopOrder) -> OrderRead:
    items = list(
        db.scalars(
            select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.id)
        )
    )
    payment = db.scalar(
        select(Payment)
        .where(Payment.order_id == order.id)
        .order_by(Payment.id.desc())
        .limit(1)
    )
    return build_order_read(order, items, payment)


@router.post("/coupons/validate", response_model=CouponResult)
def validate_coupon(
    payload: CouponValidate,
    db: Session = Depends(get_db),
) -> CouponResult:
    _, discount = calculate_coupon(db, payload.code, payload.subtotal)
    return CouponResult(
        code=payload.code.strip().upper(),
        discount=discount,
        total=payload.subtotal - discount,
    )


@router.post("/orders", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
def place_order(
    payload: OrderCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrderRead:
    if payload.payment_method != PaymentMethod.cash_on_delivery:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Online merchant checkout is not configured. "
                "Use cash_on_delivery until a verified gateway is configured."
            ),
        )

    address = db.scalar(
        select(UserAddress).where(
            UserAddress.id == payload.address_id,
            UserAddress.user_id == user.id,
        )
    )
    if address is None:
        raise HTTPException(status_code=404, detail="Shipping address not found")
    cart = db.scalar(select(Cart).where(Cart.user_id == user.id))
    if cart is None:
        raise HTTPException(status_code=400, detail="Cart is empty")
    cart_rows = db.execute(
        select(CartItem, Product)
        .join(Product, Product.id == CartItem.product_id)
        .where(CartItem.cart_id == cart.id)
    ).all()
    if not cart_rows:
        raise HTTPException(status_code=400, detail="Cart is empty")

    for item, product in cart_rows:
        shop = db.get(Shop, product.shop_id)
        category = db.get(Category, product.category_id)
        if (
            not product.is_active
            or shop is None
            or not shop.is_active
            or not shop.is_verified
            or category is None
            or not category.is_active
            or item.quantity > product.stock
        ):
            raise HTTPException(
                status_code=409,
                detail=f"Product {product.id} is unavailable or has insufficient stock",
            )
    subtotal = sum(
        (product.price * item.quantity for item, product in cart_rows),
        start=Decimal("0.00"),
    )
    coupon: Coupon | None = None
    discount = Decimal("0.00")
    coupon_code = None
    if payload.coupon_code:
        coupon, discount = calculate_coupon(db, payload.coupon_code, subtotal)
        coupon_code = coupon.code

    order = ShopOrder(
        customer_id=user.id,
        status=OrderStatus.pending,
        subtotal=subtotal,
        discount=discount,
        total=subtotal - discount,
        coupon_code=coupon_code,
        shipping_address={
            "receiver_name": address.receiver_name,
            "phone": address.phone,
            "address_line": address.address_line,
            "city": address.city,
            "postal_code": address.postal_code,
            "country": address.country,
        },
    )
    db.add(order)
    db.flush()
    for item, product in cart_rows:
        stock_update = db.execute(
            update(Product)
            .where(Product.id == product.id, Product.stock >= item.quantity)
            .values(stock=Product.stock - item.quantity)
        )
        if stock_update.rowcount != 1:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock for product {product.id}",
            )
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                shop_id=product.shop_id,
                product_name=product.name,
                unit_price=product.price,
                quantity=item.quantity,
            )
        )
        db.add(
            InventoryMovement(
                product_id=product.id,
                actor_id=user.id,
                quantity_change=-item.quantity,
                reason=f"Order #{order.id}",
            )
        )
    if coupon is not None and coupon.usage_limit is not None:
        usage_update = db.execute(
            update(Coupon)
            .where(
                Coupon.id == coupon.id,
                Coupon.usage_count < coupon.usage_limit,
                Coupon.is_active.is_(True),
            )
            .values(usage_count=Coupon.usage_count + 1)
        )
        if usage_update.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail="Coupon usage limit reached")
    elif coupon is not None:
        db.execute(
            update(Coupon)
            .where(Coupon.id == coupon.id, Coupon.is_active.is_(True))
            .values(usage_count=Coupon.usage_count + 1)
        )

    db.add(
        Payment(
            order_id=order.id,
            method=PaymentMethod.cash_on_delivery,
            status=PaymentStatus.pending,
            amount=order.total,
        )
    )
    db.add(
        OrderStatusEvent(
            order_id=order.id,
            actor_id=user.id,
            status=OrderStatus.pending,
            note="Order placed",
        )
    )
    for item, _ in cart_rows:
        db.delete(item)
    db.commit()
    db.refresh(order)
    return _order_response(db, order)


@router.get("/orders", response_model=list[OrderRead])
def list_orders(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[OrderRead]:
    orders = list(
        db.scalars(
            select(ShopOrder)
            .where(ShopOrder.customer_id == user.id)
            .order_by(ShopOrder.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return [_order_response(db, order) for order in orders]


@router.get("/orders/{order_id}", response_model=OrderRead)
def read_order(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrderRead:
    return _order_response(db, _order_or_404(db, order_id, user))


@router.get(
    "/orders/{order_id}/tracking",
    response_model=list[OrderStatusEventRead],
)
def track_order(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[OrderStatusEvent]:
    order = _order_or_404(db, order_id, user)
    return list(
        db.scalars(
            select(OrderStatusEvent)
            .where(OrderStatusEvent.order_id == order.id)
            .order_by(OrderStatusEvent.created_at)
        )
    )


@router.post("/orders/{order_id}/cancel", response_model=OrderRead)
def cancel_order(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrderRead:
    order = _order_or_404(db, order_id, user)
    if order.status not in {OrderStatus.pending, OrderStatus.confirmed}:
        raise HTTPException(status_code=409, detail="This order can no longer be cancelled")
    payment = db.scalar(select(Payment).where(Payment.order_id == order.id))
    if payment is not None and payment.status in {
        PaymentStatus.paid,
        PaymentStatus.partially_refunded,
    }:
        raise HTTPException(
            status_code=409,
            detail="Paid order cancellation requires an admin refund",
        )
    items = list(
        db.scalars(select(OrderItem).where(OrderItem.order_id == order.id))
    )
    for item in items:
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
                actor_id=user.id,
                quantity_change=item.quantity,
                reason=f"Cancelled order #{order.id}",
            )
        )
    order.status = OrderStatus.cancelled
    db.add(
        OrderStatusEvent(
            order_id=order.id,
            actor_id=user.id,
            status=OrderStatus.cancelled,
            note="Cancelled by customer",
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
    db.commit()
    db.refresh(order)
    return _order_response(db, order)


@router.get("/payments/history", response_model=list[PaymentRead])
def payment_history(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[Payment]:
    return list(
        db.scalars(
            select(Payment)
            .join(ShopOrder, Payment.order_id == ShopOrder.id)
            .where(ShopOrder.customer_id == user.id)
            .order_by(Payment.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
