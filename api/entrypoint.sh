#!/bin/sh
# DEPLOY-REQ-021: Strict startup order
# DEPLOY-REQ-022: Fail-fast on migration/seed error
set -e

echo "=== Step 1: Running database migrations ==="
alembic upgrade head

echo "=== Step 2: Running database seed ==="
python -m app.seed

echo "=== Step 3: Starting application ==="
# DEPLOY-REQ-023: Single worker (APScheduler singleton)
# DEPLOY-REQ-024: Bind 0.0.0.0 for Docker network access
# DEPLOY-REQ-025: No access log (structured app logs suffice)
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --no-access-log
