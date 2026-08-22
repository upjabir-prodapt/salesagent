#!/usr/bin/env bash
# Provisions the Firestore database backing the search query cache.
#
# Creates (idempotently):
#   - a Firestore Native-mode database
#   - a single-field index exemption for `search_results` (payloads exceed the
#     1500-byte indexed value limit, so writes fail without it)
#   - composite indexes for the cache lookup queries
#
# Usage:
#   ./scripts/create_firestore_db.sh sandbox
#   ./scripts/create_firestore_db.sh dev prod
#   ./scripts/create_firestore_db.sh all
#   ./scripts/create_firestore_db.sh --project my-gcp-project
#   ./scripts/create_firestore_db.sh --dry-run sandbox
#
# Override defaults (optional):
#   FS_PROJECT_SANDBOX=aicoesandox FS_PROJECT_DEV=aicoedev FS_PROJECT_PROD=aicoeprod \
#     FS_DATABASE='(default)' FS_LOCATION=europe-west1 FS_COLLECTION=search_cache \
#     ./scripts/create_firestore_db.sh all
#
# Requires: gcloud (authenticated) on PATH.

set -euo pipefail

readonly LOCATION="${FS_LOCATION:-europe-west1}"
readonly DATABASE="${FS_DATABASE:-(default)}"
readonly COLLECTION="${FS_COLLECTION:-search_cache}"

# Default GCP project per environment (override via env vars above).
readonly PROJECT_SANDBOX="${FS_PROJECT_SANDBOX:-aicoesandox}"
readonly PROJECT_DEV="${FS_PROJECT_DEV:-aicoedev}"
readonly PROJECT_PROD="${FS_PROJECT_PROD:-aicoeprod}"

DRY_RUN=0
CUSTOM_PROJECT=""

usage() {
  sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
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

enable_api() {
  local project="$1"
  log "Ensuring firestore.googleapis.com is enabled on ${project}"
  run gcloud services enable firestore.googleapis.com --project="$project"
}

database_exists() {
  local project="$1"
  gcloud firestore databases describe \
    --database="$DATABASE" \
    --project="$project" >/dev/null 2>&1
}

ensure_database() {
  local project="$1"

  if database_exists "$project"; then
    log "Firestore database already exists: ${project}/${DATABASE}"
    return 0
  fi

  log "Creating Firestore database ${project}/${DATABASE} (location=${LOCATION}, native mode)"
  run gcloud firestore databases create \
    --database="$DATABASE" \
    --location="$LOCATION" \
    --type=firestore-native \
    --project="$project"
}

# `search_results` holds a JSON payload well over Firestore's 1500-byte indexed
# value limit — indexing it would make every write fail.
ensure_index_exemption() {
  local project="$1"

  log "Exempting ${COLLECTION}.search_results from single-field indexing"
  run gcloud firestore indexes fields update search_results \
    --collection-group="$COLLECTION" \
    --database="$DATABASE" \
    --project="$project" \
    --disable-indexes \
    --quiet
}

# company_key + search_date  -> get_searches_for_company (ordered)
# company_key + query_hash   -> get_cached_query_hashes (IN filter)
ensure_composite_indexes() {
  local project="$1"

  log "Ensuring composite index ${COLLECTION}(company_key ASC, search_date DESC)"
  run gcloud firestore indexes composite create \
    --collection-group="$COLLECTION" \
    --database="$DATABASE" \
    --project="$project" \
    --field-config=field-path=company_key,order=ascending \
    --field-config=field-path=search_date,order=descending \
    --quiet || log "Index already exists (or creation already in progress)"

  log "Ensuring composite index ${COLLECTION}(company_key ASC, query_hash ASC)"
  run gcloud firestore indexes composite create \
    --collection-group="$COLLECTION" \
    --database="$DATABASE" \
    --project="$project" \
    --field-config=field-path=company_key,order=ascending \
    --field-config=field-path=query_hash,order=ascending \
    --quiet || log "Index already exists (or creation already in progress)"
}

provision_project() {
  local project="$1"

  log "Provisioning Firestore for project=${project} database=${DATABASE}"
  enable_api "$project"
  ensure_database "$project"
  ensure_index_exemption "$project"
  ensure_composite_indexes "$project"

  log "Done: ${project}/${DATABASE}"
  echo "  - collection: ${COLLECTION}"
  echo "  - index exemption: search_results"
  echo "  - composite: company_key ASC, search_date DESC"
  echo "  - composite: company_key ASC, query_hash ASC"
  echo
  echo "Set in your environment:"
  echo "  FIRESTORE_DATABASE=${DATABASE}"
  echo "  FIRESTORE_SEARCH_CACHE_COLLECTION=${COLLECTION}"
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
