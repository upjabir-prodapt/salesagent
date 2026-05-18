# Telemetry Runbook (Direct OTLP, No Collector)

This service exports telemetry using Google ADK/OpenTelemetry directly to Google Cloud (no OpenTelemetry Collector deployment).

## 1) Prerequisites

- Enable APIs:
  - `cloudtrace.googleapis.com`
  - `telemetry.googleapis.com`
  - `logging.googleapis.com`
  - `monitoring.googleapis.com`
- Runtime IAM:
  - `roles/telemetry.tracesWriter`
  - `roles/logging.logWriter`
  - `roles/monitoring.metricWriter`

## 2) Environment Matrix

### Local development

- `GOOGLE_CLOUD_PROJECT=<project-id>`
- `GOOGLE_APPLICATION_CREDENTIALS=<path-to-service-account-json>` or run:
  - `gcloud auth application-default login`
- `OTEL_ENABLED=true`
- `OTEL_SERVICE_NAME=sales-agent-api`
- `OTEL_EXPORTER_OTLP_ENDPOINT=https://telemetry.googleapis.com`
- `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`
- `OTEL_RESOURCE_ATTRIBUTES=service.name=sales-agent-api,gcp.project_id=<project-id>,deployment.environment=local`
- `OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true`
- Optional with user ADC:
  - `GOOGLE_CLOUD_QUOTA_PROJECT=<project-id>`

### GCP runtime (Cloud Run/GKE/VM)

- `GOOGLE_CLOUD_PROJECT=<project-id>`
- Prefer attached workload identity/service account over key file.
- `OTEL_ENABLED=true`
- `OTEL_SERVICE_NAME=sales-agent-api`
- `OTEL_EXPORTER_OTLP_ENDPOINT=https://telemetry.googleapis.com`
- `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`
- `OTEL_RESOURCE_ATTRIBUTES=service.name=sales-agent-api,gcp.project_id=<project-id>,deployment.environment=prod`
- `OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true`

## 3) GenAI Multimodal Capture (optional)

Enable when you want prompt/response payload references uploaded to GCS:

- `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`
- `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY`
- `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false`
- `OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT=jsonl`
- `OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK=upload`
- `OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH=gs://<bucket>/otel-genai`

Grant `storage.objects.create` on the upload bucket to the runtime identity.

## 4) What is instrumented

- FastAPI request handling (`FastAPIInstrumentor`).
- Background research execution span chain:
  - request accepted
  - background process
  - ADK runner lifecycle
- ADK callbacks for model/agent/tool phases as span events.
- Structured logging with trace correlation:
  - `logging.googleapis.com/trace`
  - `logging.googleapis.com/spanId`
  - `logging.googleapis.com/trace_sampled`

## 5) Smoke checks

1. Start service and call `POST /api/v1/research/initiate`.
2. In Cloud Trace:
   - verify HTTP span exists
   - verify child background/adk spans exist under same trace
3. In Cloud Logging:
   - filter logs by request/job
   - verify `logging.googleapis.com/trace` is populated
   - open linked trace from log entry
4. In Cloud Monitoring:
   - verify OTEL metrics are arriving
   - verify labels include service/resource attributes

## 6) Common failures

- `google.auth.exceptions.RefreshError`:
  - re-auth local ADC with `gcloud auth application-default login`.
- missing traces:
  - check `OTEL_ENABLED=true`
  - check `telemetry.googleapis.com` enabled and IAM role granted.
- logs not linking to traces:
  - ensure middleware context is active and structured fields are present.
