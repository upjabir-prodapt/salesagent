#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
COMPANY="${COMPANY:-Microsoft}"
ACCOUNT_ID="${ACCOUNT_ID:-ACC-MICROSOFT}"

echo "Waiting for server at ${BASE_URL}..."
for _ in $(seq 1 60); do
  if curl -sf "${BASE_URL}/health" >/dev/null 2>&1; then
    echo "Server is up"
    break
  fi
  sleep 2
done
curl -sf "${BASE_URL}/health" || { echo "Server failed to start"; exit 1; }

TOKEN=$(
  curl -sf -X POST "${BASE_URL}/api/v1/auth/token" \
    -H "Content-Type: application/json" \
    -d '{"email":"jabir.mohammed@colt.net","business_unit":"Sales","organization":"Colt"}' \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])'
)
echo "Got auth token"

RESP=$(
  curl -sf -X POST "${BASE_URL}/api/v1/research/initiate" \
    -H "Content-Type: application/json" \
    -H "x-app-auth: Bearer ${TOKEN}" \
    -d "{\"account_id\":\"${ACCOUNT_ID}\",\"company_name\":\"${COMPANY}\"}"
)
echo "Research initiated:"
echo "${RESP}" | python3 -m json.tool

JOB_ID=$(echo "${RESP}" | python3 -c 'import sys, json; print(json.load(sys.stdin)["job_id"])')
echo "Job ID: ${JOB_ID}"
echo "Polling status (Ctrl+C to stop)..."

while true; do
  STATUS=$(curl -sf "${BASE_URL}/api/v1/research/status/${JOB_ID}")
  echo "--- $(date -Is) ---"
  echo "${STATUS}" | python3 -m json.tool
  STATE=$(echo "${STATUS}" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("status", ""))')
  if echo "${STATE}" | grep -qiE 'completed|failed|error'; then
    echo "Terminal status: ${STATE}"
    break
  fi
  sleep 15
done
