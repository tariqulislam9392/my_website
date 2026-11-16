import os
from collections.abc import Generator

os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-with-at-least-32-bytes"
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = "admin@example.com"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "test-admin-password-with-more-than-12"

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, engine
from app.main import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    Base.metadata.drop_all(bind=engine)
    with TestClient(app) as test_client:
        yield test_client
    Base.metadata.drop_all(bind=engine)


def _register(client: TestClient, email: str) -> tuple[str, int]:
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Shop Owner", "email": email, "password": "valid-password-123"},
    )
    assert response.status_code == 201
    return response.json()["access_token"], response.json()["user"]["id"]


def _login(client: TestClient, email: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_seller_catalog_cart_order_coupon_payment_and_review(
    client: TestClient,
) -> None:
    admin_headers = {
        "Authorization": "Bearer "
        + _login(
            client,
            "admin@example.com",
            "test-admin-password-with-more-than-12",
        )
    }
    seller_token, _ = _register(client, "seller@example.com")
    seller_headers = {"Authorization": f"Bearer {seller_token}"}

    application = client.post(
        "/api/v1/sellers/apply",
        headers=seller_headers,
        json={"shop_name": "Ada's Shop", "description": "Test shop"},
    )
    assert application.status_code == 201
    application_id = application.json()["id"]
    approved = client.post(
        f"/api/v1/admin/seller-applications/{application_id}/approve",
        headers=admin_headers,
        json={"note": "Approved"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    category = client.post(
        "/api/v1/categories",
        headers=admin_headers,
        json={"name": "Accessories"},
    )
    assert category.status_code == 201
    category_id = category.json()["id"]

    product = client.post(
        "/api/v1/products",
        headers=seller_headers,
        json={
            "category_id": category_id,
            "name": "Canvas Bag",
            "description": "A sturdy bag",
            "price": "10.00",
            "stock": 10,
            "low_stock_threshold": 5,
            "images": ["https://example.com/bag.jpg"],
        },
    )
    assert product.status_code == 201
    product_id = product.json()["id"]
    assert client.get("/api/v1/products").json()[0]["id"] == product_id
    assert client.patch(
        f"/api/v1/products/{product_id}/stock",
        headers=seller_headers,
        json={"quantity_change": -11, "reason": "Invalid adjustment"},
    ).status_code == 409

    customer_token, _ = _register(client, "customer@example.com")
    customer_headers = {"Authorization": f"Bearer {customer_token}"}

    address = client.post(
        "/api/v1/users/me/addresses",
        headers=customer_headers,
        json={
            "receiver_name": "Shop Owner",
            "phone": "+8801712345678",
            "address_line": "1 Test Road",
            "city": "Dhaka",
            "postal_code": "1207",
            "is_default": True,
        },
    )
    assert address.status_code == 201

    assert client.post(
        f"/api/v1/wishlist/{product_id}", headers=customer_headers
    ).status_code == 201
    assert len(client.get("/api/v1/wishlist", headers=customer_headers).json()) == 1
    cart = client.post(
        "/api/v1/cart/items",
        headers=customer_headers,
        json={"product_id": product_id, "quantity": 2},
    )
    assert cart.status_code == 201
    assert cart.json()["total"] == "20.00"

    coupon = client.post(
        "/api/v1/admin/coupons",
        headers=admin_headers,
        json={
            "code": "SAVE10",
            "discount_type": "percentage",
            "discount_value": "10.00",
            "minimum_order": "0.00",
            "usage_limit": 5,
        },
    )
    assert coupon.status_code == 201
    checked = client.post(
        "/api/v1/coupons/validate",
        json={"code": "SAVE10", "subtotal": "20.00"},
    )
    assert checked.status_code == 200
    assert checked.json()["discount"] == "2.00"

    order = client.post(
        "/api/v1/orders",
        headers=customer_headers,
        json={
            "address_id": address.json()["id"],
            "coupon_code": "SAVE10",
            "payment_method": "cash_on_delivery",
        },
    )
    assert order.status_code == 201
    order_data = order.json()
    assert order_data["total"] == "18.00"
    assert order_data["payment"]["status"] == "pending"
    assert client.get("/api/v1/cart", headers=customer_headers).json()["items"] == []

    payment_id = order_data["payment"]["id"]
    verified = client.post(
        f"/api/v1/admin/payments/{payment_id}/verify",
        headers=admin_headers,
        json={"provider_reference": "COD-RECEIPT-1"},
    )
    assert verified.status_code == 200
    assert verified.json()["status"] == "paid"
    assert client.patch(
        f"/api/v1/admin/orders/{order_data['id']}/status",
        headers=admin_headers,
        json={"status": "confirmed", "note": "Confirmed"},
    ).status_code == 200
    shipped = client.patch(
        f"/api/v1/admin/orders/{order_data['id']}/status",
        headers=admin_headers,
        json={
            "status": "shipped",
            "tracking_number": "TRACK-1",
            "note": "Handed to carrier",
        },
    )
    assert shipped.status_code == 200
    assert shipped.json()["tracking_number"] == "TRACK-1"
    assert client.patch(
        f"/api/v1/admin/orders/{order_data['id']}/status",
        headers=admin_headers,
        json={"status": "delivered"},
    ).status_code == 200

    review = client.post(
        f"/api/v1/products/{product_id}/reviews",
        headers=customer_headers,
        json={"rating": 5, "comment": "Great"},
    )
    assert review.status_code == 201
    assert client.get(f"/api/v1/products/{product_id}/reviews").json()[0]["rating"] == 5
    assert client.get(
        f"/api/v1/orders/{order_data['id']}/tracking",
        headers=customer_headers,
    ).status_code == 200
    assert client.patch(
        f"/api/v1/products/{product_id}/stock",
        headers=seller_headers,
        json={"quantity_change": -4, "reason": "Store count adjustment"},
    ).status_code == 200
    assert len(
        client.get("/api/v1/inventory/low-stock", headers=seller_headers).json()
    ) == 1

    refund = client.post(
        f"/api/v1/admin/payments/{payment_id}/refund",
        headers=admin_headers,
        json={"amount": "18.00", "reason": "Customer refund"},
    )
    assert refund.status_code == 201
    dashboard = client.get("/api/v1/admin/dashboard", headers=admin_headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["total_sales"] == "0.00"

    next_cart = client.post(
        "/api/v1/cart/items",
        headers=customer_headers,
        json={"product_id": product_id, "quantity": 1},
    )
    assert next_cart.status_code == 201
    second_order = client.post(
        "/api/v1/orders",
        headers=customer_headers,
        json={
            "address_id": address.json()["id"],
            "coupon_code": "SAVE10",
        },
    )
    assert second_order.status_code == 201
    cancelled = client.post(
        f"/api/v1/orders/{second_order.json()['id']}/cancel",
        headers=customer_headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    coupons = client.get("/api/v1/admin/coupons", headers=admin_headers)
    assert coupons.json()[0]["usage_count"] == 1


def test_online_checkout_is_explicitly_disabled_without_gateway(
    client: TestClient,
) -> None:
    token, _ = _register(client, "another-customer@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post(
        "/api/v1/orders",
        headers=headers,
        json={"address_id": 1, "payment_method": "bkash"},
    )
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_local_frontend_cors_preflight(client: TestClient) -> None:
    response = client.options(
        "/api/v1/products",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_admin_can_create_shop_categories_and_public_products(client: TestClient) -> None:
    admin_headers = {
        "Authorization": "Bearer "
        + _login(
            client,
            "admin@example.com",
            "test-admin-password-with-more-than-12",
        )
    }

    shop = client.post(
        "/api/v1/admin/shops",
        headers=admin_headers,
        json={"name": "ShopHub Official", "description": "Official store"},
    )
    assert shop.status_code == 201

    category = client.post(
        "/api/v1/categories",
        headers=admin_headers,
        json={"name": "Home & Kitchen"},
    )
    assert category.status_code == 201
    category_id = category.json()["id"]

    subcategory = client.post(
        "/api/v1/categories",
        headers=admin_headers,
        json={"name": "Kitchen tools", "parent_id": category_id},
    )
    assert subcategory.status_code == 201
    all_categories = client.get(
        "/api/v1/categories", params={"include_children": "true"}
    ).json()
    assert {item["id"] for item in all_categories} == {
        category_id,
        subcategory.json()["id"],
    }

    product = client.post(
        "/api/v1/admin/products",
        headers=admin_headers,
        json={
            "shop_id": shop.json()["id"],
            "category_id": subcategory.json()["id"],
            "name": "Steel cooking pot",
            "description": "Everyday cookware",
            "price": "1250.00",
            "stock": 12,
            "low_stock_threshold": 3,
            "images": ["https://example.com/pot.jpg"],
        },
    )
    assert product.status_code == 201
    assert product.json()["shop_id"] == shop.json()["id"]
    assert client.get("/api/v1/products").json()[0]["id"] == product.json()["id"]
    assert client.get(
        "/api/v1/admin/products",
        headers=admin_headers,
        params={"shop_id": shop.json()["id"]},
    ).json()[0]["id"] == product.json()["id"]

    customer_token, _ = _register(client, "catalog-customer@example.com")
    denied = client.post(
        "/api/v1/admin/products",
        headers={"Authorization": f"Bearer {customer_token}"},
        json={
            "shop_id": shop.json()["id"],
            "category_id": category_id,
            "name": "Not allowed",
            "price": "1.00",
            "stock": 1,
        },
    )
    assert denied.status_code == 403
