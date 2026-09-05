# GitLab CI/CD Variables & Deployment Configuration Guide

This document details the GitLab CI/CD variables and Secret Manager payload keys for deploying
`sales-agent-api` and `sales-agent-worker` into **`gclt-aicoe-dev-st`**, scoped to the **`dev`**
GitLab environment, for the Apigee/AI-Hub integration built on the
`feat/agent-pipeline-rewrite-shared-dev` branch. Modelled on
[`aihub-ui`'s `GITLAB_CI_VARIABLES.md`](../shared_ui/aihub-ui/GITLAB_CI_VARIABLES.md) and
`Translation`'s own copy of this file (the two backends are near-identical in this area).

**Read this before touching either mechanism below — mixing them is the most common mistake in
this file:**

- **Section 2** = GitLab CI/CD pipeline variables (Settings → CI/CD → Variables). The `.gitlab-ci.yml`
  *script* reads these with `${VAR}` to build `gcloud` commands.
- **Section 3** = keys inside the `/secrets/.env` payload — a Secret Manager secret's *content*,
  mounted into the container at `/secrets/.env` via `--set-secrets`. The **application** reads
  these at boot, not the pipeline. `API_PREFIX`, `APIGEE_RUNTIME_SA_EMAIL`,
  `CLOUD_RUN_SERVICE_URL` and `LLM_GATEWAY_*` all belong here — they are **not** GitLab CI/CD
  variables and must **not** be added to `--set-env-vars` or to Section 2. Every credential in
  Section 3 lives only in Secret Manager, never in Git.

---

## 1. Summary

| Category | Mechanism | Notes |
|---|---|---|
| WIF & CI runner identity | GitLab CI/CD variable | Shared across all 8 platform repos |
| GCP project / region / registry | GitLab CI/CD variable | `gclt-aicoe-dev-st`, `europe-west3` |
| Cloud Run network / runtime SA | GitLab CI/CD variable | `CLOUD_RUN_API_SA` / `CLOUD_RUN_WORKER_SA`, new — see WS-F1 |
| Cloud Tasks queue / OIDC identity | GitLab CI/CD variable | `research-jobs`, `worker-invoker-sa` |
| App runtime config (prefix, Apigee trust, LLM gateway) | `/secrets/.env` payload | New in this pass — WS-D/WS-E settings |
| Pipeline access tokens | GitLab CI/CD variable (masked, protected) | Existing, unchanged |

---

## 2. GitLab CI/CD Variables (scope `dev`)

### A. Workload Identity Federation & Authentication

| Variable | Value | Source |
|---|---|---|
| `WORKLOAD_IDENTITY_PROJECT_NUMBER` | `538669417284` | `aicoe-sharedwif` project number |
| `WORKLOAD_IDENTITY_POOL` | `gitlab-pool` | Stage 0 |
| `WORKLOAD_IDENTITY_PROVIDER` | `gitlab-provider` | Stage 0 |
| `SERVICE_ACCOUNT` | `tf-deployer@gclt-aicoe-dev-st.iam.gserviceaccount.com` | Stage 0; `sales-agent` is in `allowed_repositories` (verified, WS-B8) |

### B. GCP Infrastructure

| Variable | Value | Source |
|---|---|---|
| `GCP_PROJECT_ID` | `gclt-aicoe-dev-st` (number `499193286543`) | `terraform/2-foundations/gclt-aicoe-dev-st.tf` |
| `GCP_REGION` | `europe-west3` | Platform-wide convention |
| `ARTIFACT_REPO` | `containers` | Verified CMEK Docker repo in `gclt-aicoe-dev-st` |
| `IMAGE_NAME` | `sales-agent` | |

### C. Cloud Run Service, Network & Runtime Identity

| Variable | Value | Source |
|---|---|---|
| `CLOUD_RUN_API_SERVICE` | `sales-agent-api` | WS-B1 rename — was `sales-research-application`. **Required, no fallback** — there is no shared `CLOUD_RUN_SERVICE` base name; set both this and `CLOUD_RUN_WORKER_SERVICE` explicitly |
| `CLOUD_RUN_WORKER_SERVICE` | `sales-agent-worker` | New — the worker service name didn't exist before this pass. **Required, no fallback** |
| `CLOUD_RUN_NETWORK` | `projects/gclt-aicoe-dev-network/global/networks/gclt-aicoe-dev-vpc` | Stage 3 |
| `CLOUD_RUN_SUBNET` | `projects/gclt-aicoe-dev-network/regions/europe-west3/subnetworks/gclt-aicoe-dev-cloudrun-ew3` | Stage 3; `st`'s serverless-robot SA now holds `compute.networkUser` here (WS-B7) |
| `CLOUD_RUN_API_SA` | `salesagent-sa@gclt-aicoe-dev-st.iam.gserviceaccount.com` | New CI variable name; Terraform provisions a single `salesagent-sa` for both API and worker today (unlike Translation's split SAs). **Required, no fallback** — `.gitlab-ci.yml` no longer has a shared `CLOUD_RUN_SA`; always set both this and `CLOUD_RUN_WORKER_SA`, even though they hold the same value today |
| `CLOUD_RUN_WORKER_SA` | `salesagent-sa@gclt-aicoe-dev-st.iam.gserviceaccount.com` | Same identity as above — set both so a future per-role split needs no CI change. **Required, no fallback** |

### D. App-Config Secrets & Storage

| Variable | Value | Source |
|---|---|---|
| `APP_CONFIG_API_SECRET_NAME` | `sales-agent-api-env` | Terraform stage 2, CMEK `st-ew3/secrets` |
| `APP_CONFIG_WORKER_SECRET_NAME` | `sales-agent-worker-env` | Terraform stage 2, CMEK `st-ew3/secrets` |
| `APP_CONFIG_API_SECRET_VERSION` | `latest` | Optional, defaults to `latest` if unset. Independent from the worker's version below — no shared `APP_CONFIG_SECRET_VERSION` or `SALES_AGENT_ENV_SECRET_VERSION` exists anymore, so pinning the API to an older secret version doesn't affect the worker |
| `APP_CONFIG_WORKER_SECRET_VERSION` | `latest` | Optional, defaults to `latest` if unset. Independent from the API's version above |
| `GCS_BUCKET_NAME` | `gclt-aicoe-dev-st-sales-agent` | Terraform stage 2. **Renamed 2026-09-05** — was `gclt-aicoe-dev-st-artifacts`, shared with Translation. That bucket granted both apps' service accounts `storage.objectAdmin` on the whole bucket with no prefix scoping, a real cross-app blast-radius gap. Split into dedicated per-app buckets, matching the old `aicoeprod` platform's convention (`aicoesandox-vxai-sales-app-001`). `aihub-bff-sa` still has `storage.objectAdmin` on this bucket (it uploads source documents here) |
| `ASSETS_MOUNT_PATH` | `/mnt/assets-cache` | Same value as Translation — `.gitlab-ci.yml` no longer hardcodes a fallback (`${ASSETS_MOUNT_PATH}`, bare), so this must be set explicitly and consistently across both repos |
| `GCS_ASSETS_DIR` | `assets` | Same as Translation — no hardcoded fallback in `.gitlab-ci.yml` (`${GCS_ASSETS_DIR}`, bare); set explicitly |

### E. Cloud Tasks

| Variable | Value | Source |
|---|---|---|
| `CLOUD_TASKS_QUEUE` | `research-jobs` | Terraform stage 6b (WS-B2, new). `.gitlab-ci.yml` no longer hardcodes this name as a fallback — set the variable explicitly, matching Translation's convention of not baking deployment values into the script |
| `CLOUD_TASKS_OIDC_SERVICE_ACCOUNT` | `worker-invoker-sa@gclt-aicoe-dev-st.iam.gserviceaccount.com` | Terraform stage 2; same shared invoker identity as Translation — do not create a separate one |

### F. Cache (optional, currently unset)

| Variable | Value | Source |
|---|---|---|
| `REDIS_HOST` | *(unset)* | Memorystore/Redis for `st` is deliberately deferred (GAP-REGISTER, "Deliberately deferred, not gaps") — not provisioned. `.gitlab-ci.yml` passes it through bare (`REDIS_HOST=${REDIS_HOST}`, no hardcoded fallback), so no CI change is needed if it's provisioned later |
| `REDIS_PORT` | *(unset until Memorystore exists — set to `6379` then)* | Same as above — no hardcoded default in `.gitlab-ci.yml` anymore; only matters once `REDIS_HOST` is actually set |

### G. Pipeline Access & Runner Environment (masked, protected — unchanged)

| Variable | Notes |
|---|---|
| `AZURE_PAT` | Azure DevOps PAT, Code (Read), for `sync-from-azure` |
| `GITLAB_PUSH_TOKEN` | `write_repository`, for promotion jobs |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | Corporate runner egress |

---

## 3. `/secrets/.env` Payload Keys (Secret Manager — never in Git, never a GitLab CI/CD variable)

Contents of the `sales-agent-api-env` and `sales-agent-worker-env` Secret Manager secrets (CMEK
`st-ew3/secrets`, created by Terraform stage 2; **populating the actual values is a manual,
out-of-band step**, same pattern as `AICOE-Terraform/docs/20-apigee-manual-proxy-product-app-guide.md`
Part 3).

| Key | Applies to | Value / Note |
|---|---|---|
| `API_PREFIX` | API | `/api/sales/v1` (D6 — Apigee does no path rewrite, see `AICOE-Terraform/docs/20` §5.6) |
| `APIGEE_RUNTIME_SA_EMAIL` | API | `apigee-int-runtime@gclt-aicoe-dev-apigee.iam.gserviceaccount.com` — required match for the verified Google ID token's `email` claim in `apigee_auth.py` |
| `CLOUD_RUN_SERVICE_URL` | API | This service's own `https://...run.app` URL — the ID-token audience. **Bootstrapping note:** unknown before the first deploy. Deploy once, resolve with `gcloud run services describe sales-agent-api --project=gclt-aicoe-dev-st --region=europe-west3 --format='value(status.url)'`, add it as a new secret version, then redeploy/restart. Per the `IS_LOCAL=false` boot validator, the service refuses to start at all until this is set |
| `LLM_GATEWAY_BASE_URL` | API + worker | `https://llm.aicoedev-int.colt.net` (WS-E2) — not usable until the `llm` Apigee proxy exists (GAP-REGISTER R-08); leave `LLM_GATEWAY_ENABLED=false` until then |
| `LLM_GATEWAY_API_KEY_SECRET` | API + worker | Despite the `_SECRET` suffix (kept to match the plan's naming), this holds the **raw** `sales-agent` developer app's Apigee consumer key value itself (`docs/21-apigee-llm-gateway-manual-setup-guide.md` in `AICOE-Terraform`) — appended directly into this same `sales-agent-api-env`/`sales-agent-worker-env` payload, not a pointer to a separate Secret Manager resource. This repo has no existing "fetch a named secret live at runtime" client (unlike `aihub-bff`'s `apigee_api_key_secret`), so it follows the simpler mounted-dotenv convention already used for everything else in this file |
| `LLM_GATEWAY_ENABLED` | API + worker | `false` until WS-E1's proxy is deployed **and** WS-E4 (server-side `Tool(google_search=GoogleSearch())` grounding through the proxy — the highest-risk item in the whole plan) is verified working. D-30 still forbids `roles/aiplatform.user` on this SA regardless of this flag |
| `BIGQUERY_*` | API + worker | New dataset this pass: `sales_agent_jobs` (WS-B3) |
| `GCS_PARENT_FOLDER` | API + worker | `salesagent_response` — the **job-data** parent prefix inside the dedicated bucket (`GCS_BUCKET_NAME`, §2C), e.g. `salesagent_response/{request_id}/final_report.md` (`src/shared/repositories/gcs_repository.py`). Starts empty in the new bucket by design — reports are per-request output, not asset config, so none of the old bucket's job data was copied |
| `GCS_SIGNED_URL_EXPIRATION_HOURS` | API + worker | `1` — this backend mints its own signed URLs for the final report (unlike Translation, there is no BFF-side `GcsSigner` involved on the output side) |

There is no separate "assets" prefix setting in this repo the way Translation has `GCS_ASSETS_PREFIX` —
Sales-Agent's static assets (`pricing_catalog.json`, `ColtProductCatalog.pdf`, copied from the old
`aicoesandox-vxai-sales-app-001/assets` 2026-09-05) are read from a fixed `assets/` path relative to
`ASSETS_ROOT` (§2C), not a separately-configurable GCS prefix — see `PRICING_CATALOG_FILENAME` /
`COLT_CATALOG_FILENAME` in the `/secrets/.env` payload below. Both `GCS_PARENT_FOLDER` (job data) and
the fixed `assets/` path live in the same single dedicated bucket named by `GCS_BUCKET_NAME`.

**Removed in this pass (WS-D3) — delete these keys from the secret payload, do not carry them
forward:** `IAP_AUDIENCE`, `HUB_IAP_AUDIENCE`, `SALES_REQUIRED_GROUP`, `JWT_SECRET_KEY`/`SECRET_KEY`,
`JWT_ALGORITHM`, `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, `REQUIRE_SCOPE_CLAIM`,
`SESSION_ABSOLUTE_MAX_MINUTES`. Do **not** remove `CLOUD_TASKS_OIDC_SERVICE_ACCOUNT` — that is an
unrelated worker/Cloud-Tasks setting, not part of the deleted auth surface. **Also do not reuse**
the old `sales-agent-tasks@aicoeprod...`-style service-account naming anywhere in this file or the
secret payload — the current platform convention is the shared `worker-invoker-sa@gclt-aicoe-dev-st`
identity documented in Section 2E above. If the code-side `IAP_AUDIENCE` startup validator
(`src/shared/config.py`) is not also deleted (WS-D3), the service will refuse to boot once this key
is gone from the payload.

---

## 4. Order of Operations

```
[Terraform stage 2 + 3 applied]           (done this pass — see AICOE-Terraform)
              │
              ▼
[Populate sales-agent-api-env / sales-agent-worker-env secret content — minus CLOUD_RUN_SERVICE_URL]
              │
              ▼
[GitLab CI: lint → test → build (build-and-push)]
              │
              ▼
[GitLab CI: deploy-cloud-run — worker, then API]
              │
              ▼
[Resolve real Cloud Run URLs, add CLOUD_RUN_SERVICE_URL to each secret, redeploy/restart]
              │
              ▼
[Terraform stage 6b applied]               (blocked until the two Cloud Run services above exist — GAP-REGISTER B-03)
              │
              ▼
[Apigee `int` proxy target URLs updated to the real Cloud Run URLs — docs/20 §5.1/§5.6]
              │
              ▼
[WS-G validation]
```

This branch (`feat/agent-pipeline-rewrite-shared-dev`) does not run any pipeline until merged to
`dev` (D9) — populating the variables above ahead of that merge is safe and recommended, since
nothing consumes them until then.
