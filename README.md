# FastAPI Backend

FastAPI backend with **Authentication** and **Flash Sale** services, backed by PostgreSQL and Redis.

## Tech Stack

| Layer         | Technology                                  |
| ------------- | ------------------------------------------- |
| API           | FastAPI + Uvicorn                           |
| Database      | PostgreSQL via SQLAlchemy (async) + asyncpg |
| Cache / Stock | Redis (hiredis)                             |
| Auth          | JWT (python-jose) + bcrypt                  |
| Migrations    | Alembic (async)                             |

---

## Project Structure

```
app/
├── core/           # Config, DB engine, Redis pool, JWT, dependencies
├── models/         # SQLAlchemy ORM models (User, FlashSale, FlashSalePurchase)
├── schemas/        # Pydantic request/response schemas
├── services/       # Business logic (AuthService, FlashSaleService)
├── routers/        # FastAPI routers (auth, flash_sale)
└── main.py         # App factory + lifespan
alembic/            # DB migrations
```

---

## Quick Start

### 1. Copy environment file

```bash
cp .env.example .env
# Edit .env and set a strong SECRET_KEY
```

### 2. Start PostgreSQL & Redis

```bash
docker-compose up -d
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run database migrations

```bash
alembic upgrade head
```

### 5. Start the server

```bash
uvicorn app.main:app --reload
```

Interactive docs: http://localhost:8000/docs

---

## API Reference

### Authentication `/auth`

| Method | Path             | Auth   | Description                               |
| ------ | ---------------- | ------ | ----------------------------------------- |
| POST   | `/auth/register` | —      | Register a new user                       |
| POST   | `/auth/login`    | —      | Login → returns access + refresh tokens   |
| POST   | `/auth/logout`   | Bearer | Revoke current session                    |
| POST   | `/auth/refresh`  | —      | Exchange refresh token for new token pair |
| GET    | `/auth/me`       | Bearer | Get current user profile                  |

### Flash Sales `/flash-sales`

| Method | Path                         | Auth  | Description                                      |
| ------ | ---------------------------- | ----- | ------------------------------------------------ |
| GET    | `/flash-sales/`              | User  | List flash sales (`?active_only=true`)           |
| POST   | `/flash-sales/`              | Admin | Create a flash sale                              |
| GET    | `/flash-sales/{id}`          | User  | Get flash sale details (stock synced from Redis) |
| PATCH  | `/flash-sales/{id}`          | Admin | Update flash sale                                |
| DELETE | `/flash-sales/{id}`          | Admin | Delete flash sale                                |
| POST   | `/flash-sales/{id}/purchase` | User  | Purchase items (atomic Redis stock decrement)    |
| GET    | `/flash-sales/purchases`     | User  | My purchase history                              |

---

## Key Design Decisions

- **Redis as stock source of truth**: stock is atomically decremented with `DECRBY` on every purchase. If the result goes negative it is rolled back immediately, preventing overselling.
- **Token blacklisting**: on logout the access token is stored in Redis with its remaining TTL so it cannot be reused.
- **Refresh token rotation**: every `/auth/refresh` call issues a brand-new refresh token and invalidates the old one (stored in Redis per user).
- **Per-user rate limiting**: each user is limited to 5 purchases per minute per flash sale, enforced via a Redis counter with a 60-second TTL.
- **Admin role**: set `is_admin = true` on a user row to grant admin access. Admin-only endpoints return `403` for regular users.

---

## Creating the first admin user

After running migrations, manually promote a registered user in psql:

```sql
UPDATE users SET is_admin = true WHERE email = 'admin@example.com';
```
