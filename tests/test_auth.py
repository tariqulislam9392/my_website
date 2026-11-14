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


def test_registration_login_profile_and_logout(client: TestClient) -> None:
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "name": "  Ada Shopper  ",
            "email": "ADA@example.com",
            "password": "correct-horse-battery",
        },
    )
    assert registration.status_code == 201
    user = registration.json()["user"]
    assert user["name"] == "Ada Shopper"
    assert user["email"] == "ada@example.com"
    assert user["role"] == "customer"

    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": "ada@example.com",
            "password": "correct-horse-battery",
        },
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    profile = client.get("/api/v1/users/me", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["email"] == "ada@example.com"

    logout = client.post("/api/v1/auth/logout", headers=headers)
    assert logout.status_code == 204
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401


def test_registration_rejects_duplicate_email_and_role_assignment(
    client: TestClient,
) -> None:
    payload = {
        "name": "Ada Shopper",
        "email": "ada@example.com",
        "password": "correct-horse-battery",
    }
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409

    payload["email"] = "another@example.com"
    payload["role"] = "admin"
    assert client.post("/api/v1/auth/register", json=payload).status_code == 422


def test_login_rejects_wrong_password(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/register",
        json={
            "name": "Ada Shopper",
            "email": "ada@example.com",
            "password": "correct-horse-battery",
        },
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401


def test_password_reset_invalidates_existing_access_tokens(client: TestClient) -> None:
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "name": "Reset User",
            "email": "reset@example.com",
            "password": "old-password-123",
        },
    )
    old_token = registration.json()["access_token"]
    forgotten = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "reset@example.com"},
    )
    assert forgotten.status_code == 202
    reset_token = forgotten.json()["reset_token"]
    reset = client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_token, "new_password": "new-password-123"},
    )
    assert reset.status_code == 204
    assert client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {old_token}"},
    ).status_code == 401
    assert client.post(
        "/api/v1/auth/login",
        json={"email": "reset@example.com", "password": "new-password-123"},
    ).status_code == 200


def test_customer_cannot_access_admin_and_can_deactivate_account(
    client: TestClient,
) -> None:
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "name": "Customer",
            "email": "customer@example.com",
            "password": "valid-password-123",
        },
    )
    token = registration.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/admin/dashboard", headers=headers).status_code == 403
    assert client.delete("/api/v1/users/me", headers=headers).status_code == 204
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401
