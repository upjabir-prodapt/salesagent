# 1. FROM base image
FROM python:3.11-slim

# 2. Install dependency software (uv for fast, reliable builds)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set environment variables for optimization
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8000 \
    GCE_METADATA_MTLS_MODE=none

WORKDIR /app

# 3. Copy pyproject.toml first (The Cache Trick)
COPY pyproject.toml uv.lock ./

# 4. Run uv command with pyproject.toml
# Now that torch/bert-score are removed, this is very fast!
RUN uv pip install -r pyproject.toml --system

# 5. Copy all base folder
COPY . .

# Expose the standard FastAPI port
EXPOSE 8000

# Start application using dynamic port for Cloud Run compatibility
CMD uvicorn src.routes.app:app --host 0.0.0.0 --port $PORT --workers 1
