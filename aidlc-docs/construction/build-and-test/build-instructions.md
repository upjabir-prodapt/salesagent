# Build Instructions

## Environment Setup
```bash
# Python 3.11 required
python3 -V

# Install dependencies via uv
uv sync
```

## Docker Images Build
```bash
# Public API Cloud Run image
docker build -f Dockerfile.api -t sales-agent-api .

# Internal Worker Cloud Run image
docker build -f Dockerfile.worker -t sales-agent-worker .
```

## Configuration
Copy `.env.example` to `.env` and configure:
- `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`
- `BIGQUERY_DATASET`, `BIGQUERY_TABLE`
- `GCS_BUCKET_NAME`
- `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`
