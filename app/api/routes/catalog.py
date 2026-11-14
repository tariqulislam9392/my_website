import re
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.permissions import get_active_shop
from app.core.database import get_db
from app.models.commerce import (
    InventoryMovement,
    OrderItem,
    OrderStatus,
    Product,
    Review,
    Shop,
    ShopOrder,
    Category,
)
from app.models.user import User, UserRole
from app.schemas.commerce import (
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
    ProductCreate,
    ProductRead,
    ProductUpdate,
    ReviewCreate,
    ReviewRead,
    StockAdjustment,
    InventoryMovementRead,
)

router = APIRouter(tags=["catalog"])


def _category_slug(name: str) -> str:
    return re.sub(r"[^\w-]+", "-", name.strip().lower()).strip("-")


def _require_category(db: Session, category_id: int) -> Category:
    category = db.get(Category, category_id)
    if category is None or not category.is_active:
        raise HTTPException(status_code=404, detail="Active category not found")
    return category


def _managed_product(db: Session, product_id: int, user: User) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if user.role != UserRole.admin:
        shop = db.get(Shop, product.shop_id)
        if shop is None or shop.owner_id != user.id:
            raise HTTPException(status_code=403, detail="You do not own this product")
    return product


def _public_products_query():
    return (
        select(Product)
        .join(Shop, Product.shop_id == Shop.id)
        .join(Category, Product.category_id == Category.id)
        .where(
            Product.is_active.is_(True),
            Shop.is_active.is_(True),
            Shop.is_verified.is_(True),
            Category.is_active.is_(True),
        )
    )


@router.get("/categories", response_model=list[CategoryRead])
def list_categories(
    parent_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[Category]:
    statement = select(Category).where(Category.is_active.is_(True))
    statement = statement.where(Category.parent_id == parent_id)
    return list(db.scalars(statement.order_by(Category.name)))


@router.post(
    "/categories",
    response_model=CategoryRead,
    status_code=status.HTTP_201_CREATED,
)
def create_category(
    payload: CategoryCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Category:
    if user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin permission required")
    if payload.parent_id is not None:
        _require_category(db, payload.parent_id)
    slug = payload.slug.strip().lower() if payload.slug else _category_slug(payload.name)
    if not slug:
        raise HTTPException(status_code=422, detail="A category slug is required")
    category = Category(
        name=payload.name.strip(),
        slug=slug,
        description=payload.description,
        parent_id=payload.parent_id,
    )
    db.add(category)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Category slug already exists") from None
    db.refresh(category)
    return category


@router.patch("/categories/{category_id}", response_model=CategoryRead)
def update_category(
    category_id: int,
    payload: CategoryUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Category:
    if user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin permission required")
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    updates = payload.model_dump(exclude_unset=True)
    if "parent_id" in updates and updates["parent_id"] is not None:
        parent_id = updates["parent_id"]
        if parent_id == category.id:
            raise HTTPException(status_code=422, detail="Category cannot parent itself")
        _require_category(db, parent_id)
        ancestor_id: int | None = parent_id
        while ancestor_id is not None:
            if ancestor_id == category.id:
                raise HTTPException(
                    status_code=422,
                    detail="Category hierarchy cannot contain a cycle",
                )
            parent = db.get(Category, ancestor_id)
            ancestor_id = parent.parent_id if parent is not None else None
    if "slug" in updates and updates["slug"] is not None:
        updates["slug"] = updates["slug"].strip().lower()
    if "name" in updates and updates["name"] is not None:
        updates["name"] = updates["name"].strip()
    for field, value in updates.items():
        setattr(category, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Category slug already exists") from None
    db.refresh(category)
    return category


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    if user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin permission required")
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    category.is_active = False
    db.commit()


@router.get("/products", response_model=list[ProductRead])
def list_products(
    search: str | None = Query(default=None, max_length=180),
    category_id: int | None = Query(default=None, gt=0),
    min_price: Decimal | None = Query(default=None, ge=0),
    max_price: Decimal | None = Query(default=None, ge=0),
    in_stock: bool | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[Product]:
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(status_code=422, detail="min_price cannot exceed max_price")
    statement = _public_products_query()
    if search:
        statement = statement.where(Product.name.ilike(f"%{search.strip()}%"))
    if category_id is not None:
        statement = statement.where(Product.category_id == category_id)
    if min_price is not None:
        statement = statement.where(Product.price >= min_price)
    if max_price is not None:
        statement = statement.where(Product.price <= max_price)
    if in_stock is True:
        statement = statement.where(Product.stock > 0)
    if in_stock is False:
        statement = statement.where(Product.stock == 0)
    return list(
        db.scalars(statement.order_by(Product.created_at.desc()).offset(offset).limit(limit))
    )


@router.get("/products/{product_id}", response_model=ProductRead)
def read_product(product_id: int, db: Session = Depends(get_db)) -> Product:
    product = db.scalar(_public_products_query().where(Product.id == product_id))
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.post(
    "/products",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
)
def create_product(
    payload: ProductCreate,
    shop: Shop = Depends(get_active_shop),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Product:
    _require_category(db, payload.category_id)
    product = Product(shop_id=shop.id, **payload.model_dump())
    db.add(product)
    db.flush()
    if product.stock:
        db.add(
            InventoryMovement(
                product_id=product.id,
                actor_id=user.id,
                quantity_change=product.stock,
                reason="Initial stock",
            )
        )
    db.commit()
    db.refresh(product)
    return product


@router.patch("/products/{product_id}", response_model=ProductRead)
def update_product(
    product_id: int,
    payload: ProductUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Product:
    product = _managed_product(db, product_id, user)
    updates = payload.model_dump(exclude_unset=True)
    if "category_id" in updates:
        _require_category(db, updates["category_id"])
    for field, value in updates.items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    product = _managed_product(db, product_id, user)
    product.is_active = False
    db.commit()


@router.patch("/products/{product_id}/stock", response_model=ProductRead)
def adjust_inventory(
    product_id: int,
    payload: StockAdjustment,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Product:
    product = _managed_product(db, product_id, user)
    result = db.execute(
        update(Product)
        .where(
            Product.id == product.id,
            Product.stock + payload.quantity_change >= 0,
        )
        .values(stock=Product.stock + payload.quantity_change)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Stock adjustment would make inventory negative",
        )
    db.add(
        InventoryMovement(
            product_id=product.id,
            actor_id=user.id,
            quantity_change=payload.quantity_change,
            reason=payload.reason,
        )
    )
    db.commit()
    db.refresh(product)
    return product


@router.get(
    "/products/{product_id}/inventory/movements",
    response_model=list[InventoryMovementRead],
)
def inventory_history(
    product_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[InventoryMovement]:
    product = _managed_product(db, product_id, user)
    return list(
        db.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.product_id == product.id)
            .order_by(InventoryMovement.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )


@router.get("/inventory/low-stock", response_model=list[ProductRead])
def low_stock_inventory(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Product]:
    statement = select(Product).where(
        Product.is_active.is_(True),
        Product.stock <= Product.low_stock_threshold,
    )
    if user.role == UserRole.seller:
        shop = db.scalar(select(Shop).where(Shop.owner_id == user.id))
        if shop is None:
            return []
        statement = statement.where(Product.shop_id == shop.id)
    elif user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Seller or admin permission required")
    return list(db.scalars(statement.order_by(Product.stock, Product.id)))


@router.post(
    "/products/{product_id}/reviews",
    response_model=ReviewRead,
    status_code=status.HTTP_201_CREATED,
)
def create_review(
    product_id: int,
    payload: ReviewCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Review:
    product = db.scalar(_public_products_query().where(Product.id == product_id))
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    purchased = db.scalar(
        select(OrderItem.id)
        .join(ShopOrder, ShopOrder.id == OrderItem.order_id)
        .where(
            ShopOrder.customer_id == user.id,
            ShopOrder.status == OrderStatus.delivered,
            OrderItem.product_id == product_id,
        )
        .limit(1)
    )
    if purchased is None:
        raise HTTPException(
            status_code=403,
            detail="A delivered order is required to review this product",
        )
    review = Review(
        user_id=user.id,
        product_id=product_id,
        rating=payload.rating,
        comment=payload.comment,
    )
    db.add(review)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="You have already reviewed this product",
        ) from None
    db.refresh(review)
    return review


@router.get("/products/{product_id}/reviews", response_model=list[ReviewRead])
def list_reviews(
    product_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[Review]:
    if db.get(Product, product_id) is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return list(
        db.scalars(
            select(Review)
            .where(Review.product_id == product_id)
            .order_by(Review.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
