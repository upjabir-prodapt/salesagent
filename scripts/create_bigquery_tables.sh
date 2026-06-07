#!/usr/bin/env bash
# Create BigQuery dataset and application tables for sandbox / dev / prod.
#
# Table names are identical in every project; only PROJECT (and derived dataset id) change.
#
# Usage:
#   ./scripts/create_bigquery_tables.sh sandbox
#   ./scripts/create_bigquery_tables.sh dev prod
#   ./scripts/create_bigquery_tables.sh all
#   ./scripts/create_bigquery_tables.sh --project my-gcp-project
#   ./scripts/create_bigquery_tables.sh --dry-run sandbox
#
# Override default project ids (optional):
#   BQ_PROJECT_SANDBOX=aicoesandox BQ_PROJECT_DEV=aicoedev BQ_PROJECT_PROD=aicoeprod \
#     ./scripts/create_bigquery_tables.sh all
#
# Requires: gcloud (authenticated) and bq on PATH.

set -euo pipefail

readonly LOCATION="${BQ_LOCATION:-europe-west1}"

# Table names — same in every environment.
readonly TABLE_RESEARCH_REQUESTS="research_requests"
readonly TABLE_COST_ATTRIBUTION="cost_attribution"
readonly TABLE_AGENT_TELEMETRY="agent_telemetry"
readonly TABLE_CATALOG_JOBS="catalog_build_jobs"

# Default GCP project per environment (override via env vars above).
readonly PROJECT_SANDBOX="${BQ_PROJECT_SANDBOX:-aicoesandox}"
readonly PROJECT_DEV="${BQ_PROJECT_DEV:-aicoedev}"
readonly PROJECT_PROD="${BQ_PROJECT_PROD:-aicoeprod}"

# BigQuery schemas (bq mk format: name:TYPE[:MODE]).
readonly SCHEMA_RESEARCH_REQUESTS="job_execution_id:STRING:REQUIRED,company_name:STRING:REQUIRED,status:STRING:REQUIRED,created_at:TIMESTAMP:REQUIRED,updated_at:TIMESTAMP:REQUIRED,gcs_uri:STRING,error_message:STRING,metadata:JSON,progress:INT64,current_step:STRING"
readonly SCHEMA_COST_ATTRIBUTION="job_execution_id:STRING:REQUIRED,username:STRING,email:STRING,business_unit:STRING,model_version:STRING,temperature:FLOAT64,prompt_template_version:STRING,input_tokens:INT64,output_tokens:INT64,total_tokens:INT64,latency_seconds:FLOAT64,source_domains:JSON,cost_usd:FLOAT64,created_at:TIMESTAMP:REQUIRED"
readonly SCHEMA_AGENT_TELEMETRY="record_id:STRING:REQUIRED,job_execution_id:STRING:REQUIRED,agent_name:STRING:REQUIRED,agent_type:STRING,latency_ms:INT64,tokens_input:INT64,tokens_output:INT64,sections_produced:JSON,sources_crawled:INT64,model_used:STRING,cost_usd:FLOAT64,success:BOOL,error_message:STRING,created_at:TIMESTAMP:REQUIRED"
readonly SCHEMA_CATALOG_JOBS="job_id:STRING:REQUIRED,operation:STRING:REQUIRED,status:STRING:REQUIRED,progress:INT64,current_step:STRING,version_id:STRING,error_message:STRING,user_email:STRING,created_at:TIMESTAMP:REQUIRED,updated_at:TIMESTAMP:REQUIRED,metadata:JSON"

DRY_RUN=0
CUSTOM_PROJECT=""
CUSTOM_DATASET=""

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

log() {
  printf '==> %s\n' "$*"
}

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

require_tools() {
  command -v gcloud >/dev/null 2>&1 || {
    echo "ERROR: gcloud not found on PATH" >&2
    exit 1
  }
  command -v bq >/dev/null 2>&1 || {
    echo "ERROR: bq not found on PATH (install Google Cloud SDK BigQuery component)" >&2
    exit 1
  }
}

dataset_for_project() {
  local project="$1"
  if [[ -n "$CUSTOM_DATASET" ]]; then
    printf '%s' "$CUSTOM_DATASET"
  else
    printf '%s_sales_agent_dataset' "$project"
  fi
}

project_for_env() {
  case "$1" in
    sandbox) printf '%s' "$PROJECT_SANDBOX" ;;
    dev) printf '%s' "$PROJECT_DEV" ;;
    prod) printf '%s' "$PROJECT_PROD" ;;
    *)
      echo "ERROR: Unknown environment '$1' (expected sandbox, dev, prod, or all)" >&2
      exit 1
      ;;
  esac
}

table_exists() {
  local project="$1" dataset="$2" table="$3"
  bq show "${project}:${dataset}.${table}" >/dev/null 2>&1
}

dataset_exists() {
  local project="$1" dataset="$2"
  bq show "${project}:${dataset}" >/dev/null 2>&1
}

ensure_dataset() {
  local project="$1" dataset="$2"
  local ref="${project}:${dataset}"

  if [[ "$DRY_RUN" -eq 0 ]] && dataset_exists "$project" "$dataset"; then
    log "Dataset already exists: ${ref}"
    return 0
  fi

  log "Creating dataset ${ref} (location=${LOCATION})"
  run bq mk --location="$LOCATION" "$ref"
}

ensure_partitioned_table() {
  local project="$1" dataset="$2" table="$3" schema="$4"
  local ref="${project}:${dataset}.${table}"

  if [[ "$DRY_RUN" -eq 0 ]] && table_exists "$project" "$dataset" "$table"; then
    log "Table already exists: ${ref}"
    return 0
  fi

  log "Creating table ${ref} (partitioned on created_at)"
  run bq mk --table \
    --time_partitioning_type=DAY \
    --time_partitioning_field=created_at \
    "$ref" \
    "$schema"
}

provision_project() {
  local project="$1"
  local dataset
  dataset="$(dataset_for_project "$project")"

  log "Provisioning BigQuery for project=${project} dataset=${dataset}"
  run gcloud config set project "$project" >/dev/null

  ensure_dataset "$project" "$dataset"
  ensure_partitioned_table "$project" "$dataset" "$TABLE_RESEARCH_REQUESTS" "$SCHEMA_RESEARCH_REQUESTS"
  ensure_partitioned_table "$project" "$dataset" "$TABLE_COST_ATTRIBUTION" "$SCHEMA_COST_ATTRIBUTION"
  ensure_partitioned_table "$project" "$dataset" "$TABLE_AGENT_TELEMETRY" "$SCHEMA_AGENT_TELEMETRY"
  ensure_partitioned_table "$project" "$dataset" "$TABLE_CATALOG_JOBS" "$SCHEMA_CATALOG_JOBS"

  log "Done: ${project}:${dataset}"
  echo "  - ${TABLE_RESEARCH_REQUESTS}"
  echo "  - ${TABLE_COST_ATTRIBUTION}"
  echo "  - ${TABLE_AGENT_TELEMETRY}"
  echo "  - ${TABLE_CATALOG_JOBS}"
}

main() {
  local targets=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h | --help)
        usage 0
        ;;
      -n | --dry-run)
        DRY_RUN=1
        shift
        ;;
      --project)
        [[ $# -ge 2 ]] || {
          echo "ERROR: --project requires a value" >&2
          exit 1
        }
        CUSTOM_PROJECT="$2"
        shift 2
        ;;
      --dataset)
        [[ $# -ge 2 ]] || {
          echo "ERROR: --dataset requires a value" >&2
          exit 1
        }
        CUSTOM_DATASET="$2"
        shift 2
        ;;
      all)
        targets+=(sandbox dev prod)
        shift
        ;;
      sandbox | dev | prod)
        targets+=("$1")
        shift
        ;;
      *)
        echo "ERROR: Unknown argument '$1'" >&2
        usage 1
        ;;
    esac
  done

  if [[ -n "$CUSTOM_PROJECT" ]]; then
    if [[ ${#targets[@]} -gt 0 ]]; then
      echo "ERROR: Use either --project or environment names, not both" >&2
      exit 1
    fi
    require_tools
    provision_project "$CUSTOM_PROJECT"
    return 0
  fi

  if [[ ${#targets[@]} -eq 0 ]]; then
    echo "ERROR: Specify sandbox, dev, prod, all, or --project" >&2
    usage 1
  fi

  require_tools

  local env
  for env in "${targets[@]}"; do
    provision_project "$(project_for_env "$env")"
    echo
  done
}

main "$@"
