# Stage 1: Builder - Install dependencies using uv
FROM python:3.11-slim AS builder

# CI passes --build-arg from HTTP_PROXY/HTTPS_PROXY; needed for apt + uv inside the build container
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ENV http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    no_proxy=${NO_PROXY}

# Host is RHEL 8 (ShellR2); this stage is Debian from python:3.11-slim (apt uses debian.sources).
# Corporate network blocks http to deb.debian.org — rewrite apt URIs to https before apt-get.
RUN set -e; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list.d/*.list; do \
      if [ -f "$f" ]; then \
        sed -i \
          -e 's|http://deb.debian.org|https://deb.debian.org|g' \
          -e 's|http://security.debian.org|https://security.debian.org|g' \
          "$f"; \
      fi; \
    done; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends curl ca-certificates git; \
    rm -rf /var/lib/apt/lists/*


COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project

# Stage 2: Runtime - Lean production image
FROM python:3.11-slim AS runtime

# Proxy is needed ONLY for apt-get during build; it must NOT be persisted as ENV.
# A baked-in http_proxy breaks google.auth.default() on Cloud Run (metadata
# server requests get routed to the unreachable corporate proxy → "ADC not found").
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

# WeasyPrint (report PDF generation) needs Pango/HarfBuzz at runtime.
RUN set -e; \
    export http_proxy="${HTTP_PROXY}" https_proxy="${HTTPS_PROXY:-$HTTP_PROXY}" no_proxy="${NO_PROXY}"; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list.d/*.list; do \
      if [ -f "$f" ]; then \
        sed -i \
          -e 's|http://deb.debian.org|https://deb.debian.org|g' \
          -e 's|http://security.debian.org|https://security.debian.org|g' \
          "$f"; \
      fi; \
    done; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends \
      libpango-1.0-0 \
      libpangoft2-1.0-0 \
      libharfbuzz-subset0 \
      fonts-dejavu-core \
      shared-mime-info; \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    GCE_METADATA_MTLS_MODE=none

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY . .

EXPOSE 8080

CMD ["python", "main.py"]
