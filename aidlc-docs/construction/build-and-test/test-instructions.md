# Test Instructions

## Running Tests
```bash
# Run unit tests across shared, api, and worker suites
uv run pytest tests/shared/ -v
uv run pytest tests/worker/ -v
uv run pytest tests/api/ -v

# Run full test suite with coverage gate (>=80%)
uv run pytest tests/ --cov=src --cov-fail-under=80

# Lint and formatting
uv run ruff check .
uv run ruff format . --check
```
