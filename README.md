# FastAPI Backend

FastAPI backend with **Authentication**, **Product Catalog**, and **Flash Sale** services, backed by PostgreSQL, Redis, and Celery.

---

## Tech Stack

| Layer            | Technology                                         |
| ---------------- | -------------------------------------------------- |
| API              | FastAPI 0.111 + Uvicorn                            |
| Database         | PostgreSQL 16 via SQLAlchemy 2 (async) + asyncpg   |
| Cache / Stock    | Redis 7 (hiredis) — app DB 0                       |
| Task Queue       | Celery 5.4 + Redis broker (DB 1) / backend (DB 2)  |
| Auth             | JWT access + refresh tokens (python-jose + bcrypt) |
| OTP              | 6-digit code stored in Redis (5-min TTL)           |
| Migrations       | Alembic 1.13 (async)                               |
| Containerisation | Docker + Docker Compose                            |

---

## Project Structure

```
agapi-assignment/
├── app/
│   ├── core/
│   │   ├── celery_app.py     # Celery instance (broker + backend config)
│   │   ├── config.py         # Pydantic Settings — reads .env
│   │   ├── database.py       # Async SQLAlchemy engine + session factory
│   │   ├── deps.py           # FastAPI dependency injection (auth guards)
│   │   ├── middleware.py     # AuthMiddleware — token gate for all routes
│   │   ├── otp.py            # OTP generation, Redis key helpers, regex validators
│   │   ├── redis.py          # Async Redis connection pool
│   │   └── security.py       # JWT encode/decode, bcrypt helpers
│   ├── models/
│   │   ├── base.py           # SQLAlchemy declarative base
│   │   ├── user.py           # User (email/phone, balance, roles)
│   │   ├── product.py        # Product (name, price, stock)
│   │   └── flash_sale.py     # FlashSale + FlashSalePurchase
│   ├── schemas/              # Pydantic v2 request/response schemas
│   ├── services/
│   │   ├── auth.py           # Register, login, logout, OTP, refresh
│   │   ├── product.py        # Product CRUD
│   │   ├── flash_sale.py     # Flash sale lifecycle + purchase logic
│   │   └── user.py           # User balance management
│   ├── routers/
│   │   ├── auth.py           # /auth/*
│   │   ├── product.py        # /products/*
│   │   ├── flash_sale.py     # /flash-sales/*
│   │   └── user.py           # /users/*
│   ├── tasks/
│   │   ├── flash_sale_tasks.py  # Celery task: post-sale stock sync
│   │   └── backup_tasks.py      # Celery beat task: daily pg_dump database backup
│   └── main.py               # App factory, middleware, lifespan, router registration
├── alembic/                  # Database migration scripts
├── scripts/
│   └── seed.py               # Seed 1 admin account + 10 products
├── tests/
│   ├── conftest.py           # Shared pytest fixtures (DB, Redis, user, admin, product)
│   ├── test_auth_service.py  # Auth service tests (register, login, logout, OTP, refresh)
│   ├── test_flash_sale_service.py  # Flash sale tests (CRUD, purchase, overlap guard)
│   ├── test_product_service.py     # Product CRUD tests
│   └── test_user_service.py        # User balance top-up tests
├── docker-compose.yml        # PostgreSQL + Redis + Celery worker/beat services
├── Dockerfile                # Single image for API and Celery worker
├── pytest.ini                # pytest-asyncio auto mode configuration
├── start.sh                  # Health checks → migrations → seed → uvicorn
├── requirements.txt
└── .env.example
```

---

## Quick Start

### Option A — Docker Compose (recommended)

```bash
# 1. Copy and configure environment
cp .env.example .env
# Edit .env — set a strong SECRET_KEY at minimum

# 2. Build and start all services (PostgreSQL, Redis, Celery worker, API)
docker-compose up --build
```

The API will be available at http://localhost:8000 once `start.sh` finishes its checks.

### Option B — Local development

```bash
# 1. Copy environment file
cp .env.example .env

# 2. Start infrastructure only
docker-compose up -d postgres redis

# 3. Create and activate a virtual environment
python -m venv .venv && source .venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Run migrations
alembic upgrade head

# 6. Seed initial data (admin account + 10 products)
python scripts/seed.py

# 7. Start the Celery worker (separate terminal)
celery -A app.core.celery_app.celery_app worker --loglevel=info

# 8. Start the API
uvicorn app.main:app --reload
```

Interactive docs: http://localhost:8000/docs

### start.sh startup sequence

`start.sh` is used as the Docker entrypoint and runs the following checks before starting the server:

1. **PostgreSQL** — waits up to 10 retries
2. **Redis** — connectivity + optional auth check
3. **Celery broker** — Redis DB 1 reachability (hard fail)
4. **Celery worker ping** — soft warning if no worker responds (app still starts)
5. **Alembic migrations** — `alembic upgrade head`
6. **Database seed** — `python scripts/seed.py` (idempotent)
7. **Uvicorn** — `uvicorn app.main:app --reload`

---

## Data Model & Relationships

```
┌─────────┐        ┌──────────────┐        ┌──────────────────────┐
│  users  │        │   products   │        │     flash_sales      │
├─────────┤        ├──────────────┤        ├──────────────────────┤
│ id (PK) │        │ id (PK)      │◄───────│ product_id (FK)      │
│ email   │        │ name         │        │ created_by (FK)──────┤
│ phone   │        │ description  │        │ name                 │
│ username│        │ price        │        │ sale_price           │
│ password│        │ stock        │        │ original_price       │
│ balance │        └──────────────┘        │ total_stock          │
│ is_admin│                                │ remaining_stock      │
└────┬────┘                                │ start_time           │
     │                                     │ end_time             │
     │                                     │ is_active            │
     │                                     └──────────┬───────────┘
     │                                                │
     │        ┌───────────────────────┐               │
     └────────┤  flash_sale_purchases ├───────────────┘
              ├───────────────────────┤
              │ id (PK)               │
              │ user_id (FK)          │
              │ flash_sale_id (FK)    │
              │ quantity              │
              │ unit_price            │
              │ total_price           │
              │ purchased_at          │
              └───────────────────────┘
```

**Relationships summary**

| From                | To          | Type       | Notes                                                                    |
| ------------------- | ----------- | ---------- | ------------------------------------------------------------------------ |
| `FlashSale`         | `Product`   | Many → One | `product_id` FK; `ondelete=RESTRICT` (can't delete a product with sales) |
| `FlashSale`         | `User`      | Many → One | `created_by` FK — admin who created the sale                             |
| `FlashSalePurchase` | `FlashSale` | Many → One | `ondelete=CASCADE` — purchases deleted with the sale                     |
| `FlashSalePurchase` | `User`      | Many → One | `ondelete=RESTRICT`                                                      |

---

## Authentication & Security

All routes except the ones listed below require a valid `Authorization: Bearer <token>` header, enforced by `AuthMiddleware` before the request reaches any handler.

**Public routes (no token required)**

| Path                               | Method |
| ---------------------------------- | ------ |
| `/auth/login`                      | POST   |
| `/auth/register`                   | POST   |
| `/auth/refresh`                    | POST   |
| `/auth/otp/send`                   | POST   |
| `/auth/otp/verify`                 | POST   |
| `/health`                          | GET    |
| `/docs`, `/redoc`, `/openapi.json` | GET    |

---

## API Reference

### Authentication `/auth`

| Method | Path               | Auth   | Description                                           |
| ------ | ------------------ | ------ | ----------------------------------------------------- |
| POST   | `/auth/register`   | —      | Register with email **or** phone number + password    |
| POST   | `/auth/login`      | —      | Login (identifier = email or phone); returns JWT pair |
| POST   | `/auth/logout`     | Bearer | Blacklist current access token in Redis               |
| POST   | `/auth/refresh`    | —      | Rotate refresh token → new access + refresh pair      |
| GET    | `/auth/me`         | Bearer | Get current user profile                              |
| POST   | `/auth/otp/send`   | —      | Send 6-digit OTP to email or phone (5-min TTL)        |
| POST   | `/auth/otp/verify` | —      | Verify OTP; marks email/phone as verified             |

**Register / Login identifier rules**

- Email: validated by regex — must contain `@` and a valid domain.
- Phone: E.164 format — `+` optional, 7–15 digits (e.g. `+84901234567`).
- Anything that matches neither returns `422`.

### Products `/products`

| Method | Path             | Auth  | Description           |
| ------ | ---------------- | ----- | --------------------- |
| POST   | `/products/`     | Admin | Create a product      |
| GET    | `/products/`     | User  | List all products     |
| GET    | `/products/{id}` | User  | Get product by ID     |
| PATCH  | `/products/{id}` | Admin | Update product fields |
| DELETE | `/products/{id}` | Admin | Delete product        |

### Flash Sales `/flash-sales`

| Method | Path                         | Auth  | Description                                                          |
| ------ | ---------------------------- | ----- | -------------------------------------------------------------------- |
| POST   | `/flash-sales/`              | Admin | Create flash sale (validates stock ≤ product stock, no time overlap) |
| GET    | `/flash-sales/`              | User  | List flash sales (`?active_only=true` for live sales only)           |
| GET    | `/flash-sales/{id}`          | User  | Get flash sale (remaining stock live-synced from Redis)              |
| PATCH  | `/flash-sales/{id}`          | Admin | Update flash sale (reschedules Celery task if `end_time` changes)    |
| DELETE | `/flash-sales/{id}`          | Admin | Delete flash sale + clear Redis stock key                            |
| POST   | `/flash-sales/{id}/purchase` | User  | Purchase item(s); atomic stock decrement, balance deduction          |
| GET    | `/flash-sales/purchases`     | User  | Current user's purchase history                                      |

**Purchase rules**

- User must have sufficient balance; deducted atomically on purchase.
- Stock is decremented with Redis `DECRBY`; rolled back if result < 0 (prevents overselling).
- Each user may only purchase once per flash sale per UTC day.
- Rate limit: max 5 purchase requests per user per minute per flash sale.

### Users `/users`

| Method | Path                       | Auth   | Description          |
| ------ | -------------------------- | ------ | -------------------- |
| GET    | `/users/me/balance`        | Bearer | Get current balance  |
| POST   | `/users/me/balance/top-up` | Bearer | Add funds to balance |

---

## Workflows

### Registration & Verification

```
POST /auth/register  →  account created (unverified)
POST /auth/otp/send  →  OTP stored in Redis (5 min TTL), sent to email/phone
POST /auth/otp/verify → OTP checked, deleted (single-use), user marked verified
```

### Login & Token Lifecycle

```
POST /auth/login    →  access token (30 min) + refresh token (7 days)
GET  /auth/me       →  include "Authorization: Bearer <access_token>"
POST /auth/refresh  →  old refresh token invalidated, new pair issued
POST /auth/logout   →  access token blacklisted in Redis until expiry
```

### Flash Sale Lifecycle (Admin)

```
POST /products/              →  create product with stock
POST /flash-sales/           →  create sale (stock ≤ product.stock, no time overlap)
                                └─ Celery task scheduled at end_time
PATCH /flash-sales/{id}      →  update sale (new Celery task if end_time changes)

[At end_time, Celery fires sync_product_stock_after_sale]:
  1. Reads remaining stock from Redis
  2. Adds unsold units back to product.stock
  3. Sets flash_sale.is_active = False
  4. Deletes Redis stock key
```

### Purchase Flow (User)

```
POST /users/me/balance/top-up          →  add funds
POST /flash-sales/{id}/purchase        →  validates sale window
                                          checks daily limit (Redis)
                                          checks rate limit (Redis)
                                          checks balance ≥ total_price
                                          DECRBY stock in Redis (atomic)
                                          deducts balance
                                          records FlashSalePurchase row
                                          sets daily key with TTL = seconds to midnight UTC
```

---

## Key Design Decisions

- **Redis as stock source of truth**: stock is atomically decremented with `DECRBY` on every purchase. If the result goes negative it is rolled back immediately, preventing overselling.
- **Token blacklisting**: on logout the access token is stored in Redis with its remaining TTL so it cannot be reused.
- **Refresh token rotation**: every `/auth/refresh` call issues a brand-new refresh token and invalidates the old one (stored in Redis per user).
- **OTP single-use**: once verified the OTP key is deleted from Redis; re-verification requires a new OTP.
- **Per-user rate limiting**: each user is limited to 5 purchases per minute per flash sale, enforced via a Redis counter with a 60-second TTL.
- **No time-overlapping sales**: creating a flash sale validates that no other sale for the same product overlaps the requested time window (standard interval-overlap check).
- **Celery on separate Redis DBs**: the app uses Redis DB 0; Celery broker uses DB 1 and backend DB 2 to avoid key collisions.
- **Idempotent seed**: `scripts/seed.py` checks existence before inserting; safe to run on every startup.
- **Admin account**: created automatically by `scripts/seed.py` (`admin@agapi.local` / `Admin@1234!` by default — override with `SEED_ADMIN_*` env vars).
