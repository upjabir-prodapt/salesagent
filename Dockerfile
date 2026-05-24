# Stage 1: Builder - Install dependencies using uv
FROM python:3.11-slim AS builder

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy only dependency files first (better caching)
COPY pyproject.toml uv.lock ./

# Install dependencies into a virtual environment (no dev/test)
RUN uv sync --frozen --no-dev --no-install-project

# Stage 2: Runtime - Lean production image
FROM python:3.11-slim AS runtime

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    GCE_METADATA_MTLS_MODE=none

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application source code and relevant files
COPY src/ ./src/
COPY main.py .
COPY .python-version .

# Expose Cloud Run port (default 8080, but app uses $PORT dynamically)
EXPOSE 8080

# Run the application (shell form so $PORT expands correctly)
CMD uvicorn src.routes.app:app --host 0.0.0.0 --port $PORT --workers 1
