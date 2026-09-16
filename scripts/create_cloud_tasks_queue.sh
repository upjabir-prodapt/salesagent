#!/usr/bin/env bash
# Bootstrap Cloud Tasks queue for Sales Agent research jobs.
# Everything is configuration-driven; defaults mirror production settings.
#
# Usage:
#   PROJECT=aicoeprod REGION=europe-west1 QUEUE=research-jobs \
#     ./scripts/create_cloud_tasks_queue.sh
set -euo pipefail

PROJECT="${PROJECT:?Set PROJECT}"
REGION="${REGION:-europe-west1}"
QUEUE="${QUEUE:-research-jobs}"

# Standard tier defaults (supports 30 concurrent users)
MAX_CONCURRENT="${MAX_CONCURRENT:-30}"
MAX_RATE="${MAX_RATE:-10}"
# IMPORTANT: keep in sync with the worker's CLOUD_TASKS_MAX_ATTEMPTS
# (src/shared/config.py). The worker needs this number to know when it is
# on its final delivery and must settle a transiently-failed job as
# FAILED; if the worker's value were HIGHER than this one, the last real
# delivery would leave the job non-terminal (PROCESSING) forever. Setting
# the worker's value lower is safe -- the queue's remaining deliveries
# then hit the already-terminal no-op guard in
# src/worker/api/handlers.py.
#
# These retry settings are what absorb a Vertex AI quota outage: the
# 10s-300s backoff here is minutes-scale, where the in-process agent
# backoff (src/shared/retrying.py) is seconds-scale.
MAX_ATTEMPTS="${MAX_ATTEMPTS:-5}"
MIN_BACKOFF="${MIN_BACKOFF:-10s}"

if gcloud tasks queues describe "$QUEUE" --location="$REGION" --project="$PROJECT" >/dev/null 2>&1; then
  echo "Queue $QUEUE already exists in $REGION (skipped)."
  exit 0
fi

gcloud tasks queues create "$QUEUE" \
  --location="$REGION" \
  --project="$PROJECT" \
  --max-concurrent-dispatches="$MAX_CONCURRENT" \
  --max-dispatches-per-second="$MAX_RATE" \
  --max-attempts="$MAX_ATTEMPTS" \
  --min-backoff="$MIN_BACKOFF" \
  --max-backoff=300s \
  --max-retry-duration=3600s

echo "Created queue $QUEUE (max_concurrent=$MAX_CONCURRENT, rate=${MAX_RATE}/s, max_attempts=$MAX_ATTEMPTS)."
echo "Set the worker's CLOUD_TASKS_MAX_ATTEMPTS to $MAX_ATTEMPTS to match."
echo
echo "Done. Remember to grant the API service account roles/cloudtasks.enqueuer"
echo "on the queue, and the Cloud Tasks OIDC service account roles/run.invoker on the worker."
