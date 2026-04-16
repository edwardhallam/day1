# DEPLOY-REQ-006: Two-stage build
# DEPLOY-REQ-007: Runtime based on python:3.12-slim
# DEPLOY-REQ-008: Non-root user
# DEPLOY-REQ-009: No .env or secrets in image

# --- Stage 1: Builder ---
FROM python:3.14-slim@sha256:bc389f7dfcb21413e72a28f491985326994795e34d2b86c8ae2f417b4e7818aa AS builder

WORKDIR /build

COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# --- Stage 2: Runtime ---
FROM python:3.14-slim@sha256:bc389f7dfcb21413e72a28f491985326994795e34d2b86c8ae2f417b4e7818aa

WORKDIR /app

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY alembic.ini .
COPY alembic/ alembic/
COPY app/ app/
COPY entrypoint.sh .

# DEPLOY-REQ-008: Non-root user
RUN addgroup --system appgroup && \
    adduser --system --ingroup appgroup --no-create-home appuser && \
    chmod +x entrypoint.sh

USER appuser

EXPOSE 8000

# DEPLOY-REQ-010: Shell script entrypoint
ENTRYPOINT ["./entrypoint.sh"]
