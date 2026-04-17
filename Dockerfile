# Stage 1: Builder - Install dependencies using uv
FROM python:3.11-slim AS builder

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install all dependencies (including build deps) into a virtual environment
RUN uv sync --frozen --no-install-project

# Stage 2: Runtime - Lean production image
FROM python:3.11-slim AS runtime

# Install uv in runtime image too
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    GCE_METADATA_MTLS_MODE=none \
    PORT=8080

WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install only runtime dependencies (skip dev/test)
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source code and relevant files
COPY src/ ./src/
COPY main.py .
COPY .python-version .
COPY ColtProductCatalog.pdf .

# Cloud Run defaults to port 8080, but we use the PORT env var
EXPOSE 8000

# Run the application
CMD ["uvicorn", "src.routes.app:app", "--host", "0.0.0.0", "--port", "8000"]
