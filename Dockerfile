FROM python:3.12-slim

WORKDIR /app

# System deps: libpq for psycopg2-binary, postgresql-client for pg_dump (backup task)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default: run uvicorn (overridden for celery_worker in docker-compose)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
