#!/usr/bin/env bash
# Poll research job status until COMPLETED or FAILED.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JOB_ID="${1:-$(cat "$ROOT/out/latest/google-e2e.job_id")}"
PORT="${PORT:-8081}"
INTERVAL="${INTERVAL:-30}"
LOG="$ROOT/out/latest/google-e2e-poll.log"

cd "$ROOT"
TOKEN=$(curl -s -X POST "http://localhost:8081/api/v1/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"email":"e2e-test@colt.net","business_unit":"test","organization":"test"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "=== Polling job_id=$JOB_ID every ${INTERVAL}s ===" | tee -a "$LOG"
while true; do
  TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  BODY=$(curl -s -H "x-app-auth: Bearer ${TOKEN}" \
    "http://localhost:${PORT}/api/v1/research/status/${JOB_ID}")
  STATUS=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "ERROR")
  echo "[$TS] $BODY" | tee -a "$LOG"
  if [[ "$STATUS" == "COMPLETED" || "$STATUS" == "FAILED" ]]; then
    echo "[$TS] Terminal status: $STATUS" | tee -a "$LOG"
    exit 0
  fi
  sleep "$INTERVAL"
done
