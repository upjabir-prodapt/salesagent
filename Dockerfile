# 1. FROM base image
FROM python:3.11-slim

# 2. Install dependency software (uv for fast, reliable builds)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set environment variables for optimization
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8000 \
    GCE_METADATA_MTLS_MODE=none

WORKDIR /app

# 3. Copy dependency files
COPY pyproject.toml uv.lock ./

# 4. Install dependencies into a virtual environment
# This ensures we use the exact versions from uv.lock
RUN uv sync --frozen --no-dev --no-install-project

# 5. Copy all base folder
COPY . .

# Expose the standard FastAPI port
EXPOSE 8000

# Start application using dynamic port for Cloud Run compatibility
CMD uvicorn src.routes.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
