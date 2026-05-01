#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# start.sh — check Postgres + Redis connectivity, run migrations, then serve
# ---------------------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ---------------------------------------------------------------------------
# Load .env if present
# ---------------------------------------------------------------------------
if [ -f ".env" ]; then
    log_info "Loading environment from .env"
    set -o allexport
    # shellcheck disable=SC1091
    source .env
    set +o allexport
else
    log_warn ".env file not found — using existing environment variables"
fi

# Defaults (must match app/core/config.py defaults)
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-fastapi_db}"

REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"
CELERY_BROKER_DB="${CELERY_BROKER_DB:-1}"

MAX_RETRIES=10
RETRY_INTERVAL=3   # seconds

# ---------------------------------------------------------------------------
# 1. Wait for PostgreSQL
# ---------------------------------------------------------------------------
log_info "Checking PostgreSQL connection (${POSTGRES_HOST}:${POSTGRES_PORT})..."

attempt=0
until PGPASSWORD="$POSTGRES_PASSWORD" psql \
    -h "$POSTGRES_HOST" \
    -p "$POSTGRES_PORT" \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    -c "SELECT 1" \
    --no-password \
    -q \
    > /dev/null 2>&1; do

    attempt=$(( attempt + 1 ))
    if [ "$attempt" -ge "$MAX_RETRIES" ]; then
        log_error "Could not connect to PostgreSQL after ${MAX_RETRIES} attempts. Aborting."
        exit 1
    fi
    log_warn "PostgreSQL not ready (attempt ${attempt}/${MAX_RETRIES}). Retrying in ${RETRY_INTERVAL}s..."
    sleep "$RETRY_INTERVAL"
done

log_info "PostgreSQL is up."

# ---------------------------------------------------------------------------
# 2. Wait for Redis
# ---------------------------------------------------------------------------
log_info "Checking Redis connection (${REDIS_HOST}:${REDIS_PORT})..."

attempt=0
until redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping > /dev/null 2>&1; do
    attempt=$(( attempt + 1 ))
    if [ "$attempt" -ge "$MAX_RETRIES" ]; then
        log_error "Could not connect to Redis after ${MAX_RETRIES} attempts. Aborting."
        exit 1
    fi
    log_warn "Redis not ready (attempt ${attempt}/${MAX_RETRIES}). Retrying in ${RETRY_INTERVAL}s..."
    sleep "$RETRY_INTERVAL"
done

log_info "Redis is up."

# If REDIS_PASSWORD is set, verify auth
if [ -n "${REDIS_PASSWORD:-}" ]; then
    if ! redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -a "$REDIS_PASSWORD" ping > /dev/null 2>&1; then
        log_error "Redis authentication failed. Check REDIS_PASSWORD."
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# 3. Check Celery Broker (Redis DB used by Celery)
# ---------------------------------------------------------------------------
log_info "Checking Celery broker connection (Redis DB ${CELERY_BROKER_DB})..."

REDIS_AUTH_ARGS=()
if [ -n "${REDIS_PASSWORD:-}" ]; then
    REDIS_AUTH_ARGS=(-a "$REDIS_PASSWORD")
fi

if ! redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" \
        ${REDIS_AUTH_ARGS[@]+"${REDIS_AUTH_ARGS[@]}"} \
        -n "$CELERY_BROKER_DB" ping > /dev/null 2>&1; then
    log_error "Cannot reach Celery broker (Redis DB ${CELERY_BROKER_DB}). Aborting."
    exit 1
fi
log_info "Celery broker is reachable."

# ---------------------------------------------------------------------------
# 4. Celery Worker Health Check (best-effort — warns but does not block)
# ---------------------------------------------------------------------------
log_info "Pinging Celery workers..."
CELERY_WORKER_TIMEOUT="${CELERY_WORKER_TIMEOUT:-5}"

if celery -A app.core.celery_app.celery_app inspect ping \
        --timeout "$CELERY_WORKER_TIMEOUT" > /dev/null 2>&1; then
    log_info "At least one Celery worker responded."
else
    log_warn "No Celery workers responded within ${CELERY_WORKER_TIMEOUT}s. " \
             "Workers may not be running yet — the app will start anyway."
fi

# ---------------------------------------------------------------------------
# 5. Run Alembic migrations
# ---------------------------------------------------------------------------
log_info "Running database migrations (alembic upgrade head)..."
if ! alembic upgrade head; then
    log_error "Alembic migrations failed. Aborting."
    exit 1
fi
log_info "Database is up to date."

# ---------------------------------------------------------------------------
# 6. Seed the database
# ---------------------------------------------------------------------------
log_info "Running database seed (python scripts/seed.py)..."
if ! python scripts/seed.py; then
    log_error "Database seeding failed. Aborting."
    exit 1
fi

# ---------------------------------------------------------------------------
# 7. Start the backend
# ---------------------------------------------------------------------------
log_info "Starting FastAPI with uvicorn..."
exec uvicorn app.main:app --reload
