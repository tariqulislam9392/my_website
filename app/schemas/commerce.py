from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.models.commerce import (
    ApplicationStatus,
    CouponType,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
)


class AddressInput(BaseModel):
    receiver_name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=5, max_length=32)
    address_line: str = Field(min_length=3, max_length=255)
    city: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=20)
    country: str = Field(default="Bangladesh", min_length=1, max_length=80)
    is_default: bool = False


class AddressRead(AddressInput):
    id: int

    model_config = ConfigDict(from_attributes=True)


class SellerApplicationCreate(BaseModel):
    shop_name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)


class SellerApplicationRead(BaseModel):
    id: int
    user_id: int
    shop_name: str
    description: str
    status: ApplicationStatus
    submitted_at: datetime
    decided_at: datetime | None
    decision_note: str | None

    model_config = ConfigDict(from_attributes=True)


class ShopUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_changes(self) -> "ShopUpdate":
        if not self.model_fields_set or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("Provide non-null shop fields to update")
        return self


class ShopRead(BaseModel):
    id: int
    owner_id: int
    name: str
    description: str
    is_verified: bool
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SellerDecision(BaseModel):
    note: str = Field(default="", max_length=500)


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str | None = Field(default=None, max_length=120)
    description: str = Field(default="", max_length=2000)
    parent_id: int | None = Field(default=None, gt=0)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Category name must not be empty")
        return value


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    parent_id: int | None = Field(default=None, gt=0)
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_changes(self) -> "CategoryUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one category field to update")
        return self

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Category name must not be empty")
        return value


class CategoryRead(BaseModel):
    id: int
    parent_id: int | None
    name: str
    slug: str
    description: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class ProductCreate(BaseModel):
    category_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=10_000)
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    stock: int = Field(default=0, ge=0)
    low_stock_threshold: int = Field(default=5, ge=0)
    images: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Product name must not be empty")
        return value

    @model_validator(mode="after")
    def validate_images(self) -> "ProductCreate":
        if any(not item.startswith(("https://", "http://")) for item in self.images):
            raise ValueError("Product images must be http(s) URLs")
        return self


class ProductUpdate(BaseModel):
    category_id: int | None = Field(default=None, gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=10_000)
    price: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    low_stock_threshold: int | None = Field(default=None, ge=0)
    images: list[str] | None = Field(default=None, max_length=10)
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_changes(self) -> "ProductUpdate":
        if not self.model_fields_set or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("Provide non-null product fields to update")
        if self.images is not None and any(
            not item.startswith(("https://", "http://")) for item in self.images
        ):
            raise ValueError("Product images must be http(s) URLs")
        return self

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Product name must not be empty")
        return value


class StockAdjustment(BaseModel):
    quantity_change: int
    reason: str = Field(min_length=3, max_length=255)

    @model_validator(mode="after")
    def require_nonzero_change(self) -> "StockAdjustment":
        if self.quantity_change == 0:
            raise ValueError("Stock adjustment must not be zero")
        return self


class ProductRead(BaseModel):
    id: int
    shop_id: int
    category_id: int
    name: str
    description: str
    price: Decimal
    stock: int
    low_stock_threshold: int
    images: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CartItemInput(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=999)


class CartItemUpdate(BaseModel):
    quantity: int = Field(gt=0, le=999)


class CartItemRead(BaseModel):
    product_id: int
    product_name: str
    unit_price: Decimal
    quantity: int
    line_total: Decimal


class CartRead(BaseModel):
    items: list[CartItemRead]
    total: Decimal


class OrderCreate(BaseModel):
    address_id: int = Field(gt=0)
    coupon_code: str | None = Field(default=None, max_length=40)
    payment_method: PaymentMethod = PaymentMethod.cash_on_delivery


class OrderItemRead(BaseModel):
    product_id: int | None
    shop_id: int
    product_name: str
    unit_price: Decimal
    quantity: int

    model_config = ConfigDict(from_attributes=True)


class PaymentRead(BaseModel):
    id: int
    order_id: int
    method: PaymentMethod
    status: PaymentStatus
    amount: Decimal
    provider_reference: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrderRead(BaseModel):
    id: int
    customer_id: int
    status: OrderStatus
    subtotal: Decimal
    discount: Decimal
    total: Decimal
    coupon_code: str | None
    shipping_address: dict[str, str]
    tracking_number: str | None
    created_at: datetime
    items: list[OrderItemRead]
    payment: PaymentRead | None = None


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
    tracking_number: str | None = Field(default=None, max_length=100)
    note: str = Field(default="", max_length=255)


class OrderStatusEventRead(BaseModel):
    status: OrderStatus
    note: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CouponCreate(BaseModel):
    code: str = Field(min_length=3, max_length=40)
    discount_type: CouponType
    discount_value: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    max_discount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    minimum_order: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=12, decimal_places=2)
    usage_limit: int | None = Field(default=None, gt=0)
    starts_at: datetime | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_coupon(self) -> "CouponCreate":
        if self.discount_type == CouponType.percentage and self.discount_value > 100:
            raise ValueError("Percentage discount cannot exceed 100")
        if (
            self.starts_at is not None
            and self.expires_at is not None
            and self.expires_at <= self.starts_at
        ):
            raise ValueError("Coupon expiry must be after its start time")
        return self


class CouponUpdate(BaseModel):
    is_active: bool


class CouponValidate(BaseModel):
    code: str = Field(min_length=3, max_length=40)
    subtotal: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class CouponRead(BaseModel):
    id: int
    code: str
    discount_type: CouponType
    discount_value: Decimal
    max_discount: Decimal | None
    minimum_order: Decimal
    usage_limit: int | None
    usage_count: int
    starts_at: datetime | None
    expires_at: datetime | None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class CouponResult(BaseModel):
    code: str
    discount: Decimal
    total: Decimal


class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=3000)


class ReviewRead(BaseModel):
    id: int
    user_id: int
    product_id: int
    rating: int
    comment: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RefundCreate(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reason: str = Field(min_length=3, max_length=255)


class PaymentVerification(BaseModel):
    provider_reference: str | None = Field(default=None, max_length=120)


class RefundRead(BaseModel):
    id: int
    payment_id: int
    admin_id: int
    amount: Decimal
    reason: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AdminDashboard(BaseModel):
    total_users: int
    total_sellers: int
    total_products: int
    total_orders: int
    total_sales: Decimal
    pending_seller_applications: int
    pending_orders: int


class AdminUserRead(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserActiveUpdate(BaseModel):
    is_active: bool


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)


class GenericMessage(BaseModel):
    message: str
    reset_token: str | None = None


class SellerDashboard(BaseModel):
    shop_id: int
    total_products: int
    total_order_items: int
    total_sales: Decimal
    low_stock_products: int


class InventoryMovementRead(BaseModel):
    id: int
    product_id: int
    actor_id: int
    quantity_change: int
    reason: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


def build_order_read(
    order: Any,
    items: list[Any],
    payment: Any | None,
) -> OrderRead:
    return OrderRead(
        id=order.id,
        customer_id=order.customer_id,
        status=order.status,
        subtotal=order.subtotal,
        discount=order.discount,
        total=order.total,
        coupon_code=order.coupon_code,
        shipping_address=order.shipping_address,
        tracking_number=order.tracking_number,
        created_at=order.created_at,
        items=[OrderItemRead.model_validate(item) for item in items],
        payment=PaymentRead.model_validate(payment) if payment is not None else None,
    )
