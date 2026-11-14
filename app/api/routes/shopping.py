from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.commerce import (
    Cart,
    CartItem,
    Category,
    Product,
    Shop,
    WishlistItem,
)
from app.models.user import User
from app.schemas.commerce import CartItemInput, CartItemRead, CartItemUpdate, CartRead, ProductRead

router = APIRouter(tags=["shopping"])


def _get_cart(db: Session, user_id: int) -> Cart:
    cart = db.scalar(select(Cart).where(Cart.user_id == user_id))
    if cart is None:
        cart = Cart(user_id=user_id)
        db.add(cart)
        db.flush()
    return cart


def _available_product(db: Session, product_id: int) -> Product:
    product = db.scalar(
        select(Product)
        .join(Shop, Product.shop_id == Shop.id)
        .join(Category, Product.category_id == Category.id)
        .where(
            Product.id == product_id,
            Product.is_active.is_(True),
            Shop.is_active.is_(True),
            Shop.is_verified.is_(True),
            Category.is_active.is_(True),
        )
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Available product not found")
    return product


@router.get("/cart", response_model=CartRead)
def read_cart(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CartRead:
    cart = db.scalar(select(Cart).where(Cart.user_id == user.id))
    if cart is None:
        return CartRead(items=[], total=Decimal("0.00"))
    rows = db.execute(
        select(CartItem, Product)
        .join(Product, Product.id == CartItem.product_id)
        .join(Shop, Shop.id == Product.shop_id)
        .join(Category, Category.id == Product.category_id)
        .where(CartItem.cart_id == cart.id)
        .where(
            Product.is_active.is_(True),
            Shop.is_active.is_(True),
            Shop.is_verified.is_(True),
            Category.is_active.is_(True),
        )
        .order_by(CartItem.id)
    ).all()
    items = [
        CartItemRead(
            product_id=product.id,
            product_name=product.name,
            unit_price=product.price,
            quantity=item.quantity,
            line_total=product.price * item.quantity,
        )
        for item, product in rows
        if product.is_active
    ]
    return CartRead(
        items=items,
        total=sum((item.line_total for item in items), start=Decimal("0.00")),
    )


@router.post("/cart/items", response_model=CartRead, status_code=201)
def add_cart_item(
    payload: CartItemInput,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CartRead:
    product = _available_product(db, payload.product_id)
    cart = _get_cart(db, user.id)
    item = db.scalar(
        select(CartItem).where(
            CartItem.cart_id == cart.id,
            CartItem.product_id == product.id,
        )
    )
    new_quantity = payload.quantity + (item.quantity if item is not None else 0)
    if new_quantity > product.stock:
        raise HTTPException(status_code=409, detail="Requested quantity exceeds stock")
    if item is None:
        db.add(CartItem(cart_id=cart.id, product_id=product.id, quantity=new_quantity))
    else:
        item.quantity = new_quantity
    db.commit()
    return read_cart(user, db)


@router.patch("/cart/items/{product_id}", response_model=CartRead)
def update_cart_item(
    product_id: int,
    payload: CartItemUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CartRead:
    product = _available_product(db, product_id)
    if payload.quantity > product.stock:
        raise HTTPException(status_code=409, detail="Requested quantity exceeds stock")
    cart = db.scalar(select(Cart).where(Cart.user_id == user.id))
    item = (
        db.scalar(
            select(CartItem).where(
                CartItem.cart_id == cart.id,
                CartItem.product_id == product.id,
            )
        )
        if cart is not None
        else None
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Cart item not found")
    item.quantity = payload.quantity
    db.commit()
    return read_cart(user, db)


@router.delete("/cart/items/{product_id}", response_model=CartRead)
def remove_cart_item(
    product_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CartRead:
    cart = db.scalar(select(Cart).where(Cart.user_id == user.id))
    item = (
        db.scalar(
            select(CartItem).where(
                CartItem.cart_id == cart.id,
                CartItem.product_id == product_id,
            )
        )
        if cart is not None
        else None
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Cart item not found")
    db.delete(item)
    db.commit()
    return read_cart(user, db)


@router.get("/wishlist", response_model=list[ProductRead])
def read_wishlist(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Product]:
    return list(
        db.scalars(
            select(Product)
            .join(WishlistItem, WishlistItem.product_id == Product.id)
            .join(Shop, Shop.id == Product.shop_id)
            .join(Category, Category.id == Product.category_id)
            .where(
                WishlistItem.user_id == user.id,
                Product.is_active.is_(True),
                Shop.is_active.is_(True),
                Shop.is_verified.is_(True),
                Category.is_active.is_(True),
            )
            .order_by(WishlistItem.id.desc())
        )
    )


@router.post("/wishlist/{product_id}", status_code=status.HTTP_201_CREATED)
def add_wishlist_item(
    product_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    product = _available_product(db, product_id)
    existing = db.scalar(
        select(WishlistItem).where(
            WishlistItem.user_id == user.id,
            WishlistItem.product_id == product.id,
        )
    )
    if existing is None:
        db.add(WishlistItem(user_id=user.id, product_id=product.id))
        db.commit()
    return {"message": "Product is in your wishlist"}


@router.delete("/wishlist/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_wishlist_item(
    product_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    item = db.scalar(
        select(WishlistItem).where(
            WishlistItem.user_id == user.id,
            WishlistItem.product_id == product_id,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Wishlist item not found")
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
