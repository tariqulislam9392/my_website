# ShopHub Backend

ShopHub is a FastAPI marketplace backend. It includes JWT authentication,
customer accounts and addresses, seller applications and approval, shops,
categories/subcategories, products and inventory, cart, wishlist, orders,
tracking, coupons, reviews, payment records, refunds, and admin/seller dashboards.

## Run locally

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
uvicorn main:app --reload
```

By default, local development uses SQLite and creates `shophub.db`. PostgreSQL
is supported; set `DATABASE_URL` in `.env`, for example:

```text
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/shophub
```

The API docs are available at `/docs`.

## First admin

Public registration always creates a customer account. To create the first
admin, add both of these settings to the private `.env` file before starting
the app:

```text
BOOTSTRAP_ADMIN_EMAIL=admin@example.com
BOOTSTRAP_ADMIN_PASSWORD=use-a-unique-password-of-at-least-12-characters
```

The admin is created once when the app starts. Do not commit `.env`. For
production, also set `ENVIRONMENT=production` and a random `JWT_SECRET_KEY` of
at least 32 characters. SMTP settings (`SMTP_HOST`, `SMTP_PORT`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_SENDER`) are required to send password
reset email in production. In development/test, the reset token is returned by
the forgot-password endpoint to support local testing.

## Main API groups

- Authentication: `/api/v1/auth/*` — register, login, logout, forgot/reset password
- Customer profile and addresses: `/api/v1/users/me*`
- Seller applications, shop profile/dashboard: `/api/v1/sellers/*`
- Public shops, categories, products, reviews: `/api/v1/shops*`,
  `/api/v1/categories*`, `/api/v1/products*`
- Inventory: `/api/v1/inventory/low-stock` and
  `/api/v1/products/{id}/stock`
- Cart and wishlist: `/api/v1/cart*`, `/api/v1/wishlist*`
- Orders and tracking: `/api/v1/orders*`
- Coupon validation: `POST /api/v1/coupons/validate`
- Admin operations: `/api/v1/admin/*` — dashboard, users, seller approval,
  shops, orders/status, coupons, payment verification and refunds
- Health: `/health`

All amounts are stored as decimal BDT values. Product images are accepted as
HTTP(S) URLs; binary image upload/storage needs an object-storage service.
Cash on delivery is available. Online order checkout intentionally returns
`503` until an approved SSLCommerz or bKash merchant integration is configured.
Admin payment verification and refund endpoints maintain internal records;
they do not call a payment provider or transfer money.

The starter app creates tables with SQLAlchemy `create_all` at startup. Before
production, replace this with versioned Alembic migrations, configure PostgreSQL,
SMTP and a merchant gateway, and review operational controls such as rate
limiting, backups and deployment secrets.

## Tests

```bash
python -m pytest -q
```
