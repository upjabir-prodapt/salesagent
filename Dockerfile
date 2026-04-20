# Stage 1: Builder - Build and prepare dependencies
FROM python:3.11-slim AS builder

# Optimized: Use uv to resolve and install dependencies with high parallelism
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy dependency files first to leverage build cache
COPY pyproject.toml uv.lock ./

# Optimized: Force install CPU-only torch to save ~1.5GB of image size
# Azure DevOps agents have limited space; this is the single most important optimization
RUN uv pip install torch --index-url https://download.pytorch.org/whl/cpu --system

# Install remaining dependencies from pyproject.toml
RUN uv pip install -r pyproject.toml --system

# Copy application and download script
COPY download_models.py .
COPY src/ ./src/

# Optimized: Pre-download BERT models during build time
# This prevents timeouts and large downloads during first request/runtime
RUN python download_models.py

# Stage 2: Runtime - Lean production image
FROM python:3.11-slim AS runtime

# Set runtime environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    GCE_METADATA_MTLS_MODE=none

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy pre-downloaded HuggingFace models cache
COPY --from=builder /root/.cache/huggingface /root/.cache/huggingface

# Copy application source code and relevant assets
COPY src/ ./src/
COPY main.py .
COPY .python-version .
COPY ColtProductCatalog.pdf .

# Expose port 8080 (Cloud Run / Default)
EXPOSE 8080

# Run the application
# Use workers=1 for memory-constrained environments unless CPU allows more
CMD ["uvicorn", "src.routes.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
