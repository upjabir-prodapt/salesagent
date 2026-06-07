#!/usr/bin/env bash
# Replicate GitLab Workload Identity Federation from aicoesandox to another GCP project.
# Requires: gcloud auth with roles/owner (or iam.workloadIdentityPoolAdmin + iam.serviceAccountAdmin)
# on the target project.
set -euo pipefail

PROJECT_ID="${1:-aicoedev}"
POOL_ID="gitlab-pool"
PROVIDER_ID="gitlab-provider"
DEPLOYER_SA="gitlab-deployer"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JWKS_FILE="${SCRIPT_DIR}/gitlab-jwks.json"

if [[ ! -f "$JWKS_FILE" ]]; then
  echo "ERROR: Missing $JWKS_FILE"
  exit 1
fi

echo "Target project: $PROJECT_ID"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
echo "Project number: $PROJECT_NUMBER"

echo "Creating workload identity pool ${POOL_ID}..."
gcloud iam workload-identity-pools create "$POOL_ID" \
  --project="$PROJECT_ID" \
  --location=global \
  --display-name="gitlab-pool" \
  --description="GitLab CI/CD (amsgit01) WIF pool" \
  2>/dev/null || echo "Pool ${POOL_ID} already exists, continuing..."

echo "Creating OIDC provider ${PROVIDER_ID}..."
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
  --project="$PROJECT_ID" \
  --location=global \
  --workload-identity-pool="$POOL_ID" \
  --display-name="gitlab-provider" \
  --issuer-uri="https://amsgit01" \
  --allowed-audiences="https://iam.googleapis.com" \
  --attribute-mapping="google.subject=assertion.sub" \
  --jwk-json-path="$JWKS_FILE" \
  2>/dev/null || echo "Provider ${PROVIDER_ID} already exists, continuing..."

echo "Creating service account ${DEPLOYER_SA}@${PROJECT_ID}.iam.gserviceaccount.com..."
gcloud iam service-accounts create "$DEPLOYER_SA" \
  --project="$PROJECT_ID" \
  --display-name="gitlab-deployer" \
  2>/dev/null || echo "Service account already exists, continuing..."

SA_EMAIL="${DEPLOYER_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
POOL_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}"

echo "Binding WIF principals to ${SA_EMAIL}..."
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project="$PROJECT_ID" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principal://iam.googleapis.com/${POOL_RESOURCE}/subject/*"

gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project="$PROJECT_ID" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --member="principalSet://iam.googleapis.com/${POOL_RESOURCE}/*"

echo "Granting deployer Owner on ${PROJECT_ID} (matches aicoesandox)..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/owner"

echo ""
echo "Done. GitLab CI/CD variables for ${PROJECT_ID}:"
echo "  WORKLOAD_IDENTITY_PROJECT_NUMBER=${PROJECT_NUMBER}"
echo "  WORKLOAD_IDENTITY_POOL=${POOL_ID}"
echo "  WORKLOAD_IDENTITY_PROVIDER=${PROVIDER_ID}"
echo "  SERVICE_ACCOUNT=${SA_EMAIL}"
echo "  GCP_PROJECT_ID=${PROJECT_ID}"
