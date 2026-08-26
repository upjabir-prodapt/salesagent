"""
OpenTelemetry bootstrap for custom FastAPI + ADK (Plan 2).

ADK's Runner uses trace.get_tracer(), which resolves to the global TracerProvider.
Call setup_telemetry() once at startup, before any Runner.run_async() call.
GoogleGenAiSdkInstrumentor is required here because ADK only enables it inside
get_fast_api_app / AdkWebServer.
"""

import logging

import google.auth
from google.auth.transport.requests import AuthorizedSession
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import OTELResourceDetector, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .config import settings

logger = logging.getLogger(__name__)


def _patch_otel_safe_detach() -> None:
    """Avoid noisy detach errors when ADK async generators close in another context.

    ParallelAgent builds sub-agent async generators in the parent task, then
    iterates them in TaskGroup children. On GeneratorExit/aclose, OTEL span
    tokens may not match the current contextvars context. Spans are already
    ended; failed detach is benign.
    """
    import opentelemetry.context as ctx_module

    if getattr(ctx_module, "_sales_agent_detach_patched", False):
        return

    def detach(token):  # noqa: ANN001
        try:
            return ctx_module._RUNTIME_CONTEXT.detach(token)
        except ValueError:
            return None

    ctx_module.detach = detach
    ctx_module._sales_agent_detach_patched = True
    logger.info("OTEL context detach patched for ADK async generators")


def _log_otel_content_capture_config() -> None:
    """Validate and log OTEL content-capture settings at startup."""
    semconv = settings.OTEL_SEMCONV_STABILITY_OPT_IN
    capture_mode = settings.OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT
    adk_spans = "true" if settings.ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS else "false"

    _VALID_CAPTURE_MODES = {
        "",
        "NO_CONTENT",
        "SPAN_ONLY",
        "EVENT_ONLY",
        "SPAN_AND_EVENT",
    }
    if capture_mode.upper() not in _VALID_CAPTURE_MODES:
        logger.warning(
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT has unrecognised value %r "
            "(valid: %s) — falling back to NO_CONTENT",
            capture_mode,
            ", ".join(sorted(_VALID_CAPTURE_MODES - {""})),
        )

    effective_capture = (
        capture_mode.upper()
        if capture_mode.upper() in _VALID_CAPTURE_MODES
        else "NO_CONTENT"
    )
    experimental_mode = "gen_ai_latest_experimental" in semconv

    logger.info(
        "OTEL content capture config: "
        "OTEL_SEMCONV_STABILITY_OPT_IN=%r "
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=%r (effective=%s) "
        "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=%r "
        "experimental_semconv=%s",
        semconv,
        capture_mode,
        effective_capture,
        adk_spans,
        experimental_mode,
    )

    if not semconv:
        logger.warning(
            "OTEL_SEMCONV_STABILITY_OPT_IN is not set; "
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT will use legacy "
            "true/false interpretation only."
        )
    if effective_capture in ("EVENT_ONLY", "SPAN_AND_EVENT") and not experimental_mode:
        logger.warning(
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=%r requires "
            "OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental to take effect.",
            capture_mode,
        )


def setup_telemetry() -> None:
    """Configure global TracerProvider exporting OTLP/HTTP to Cloud Trace."""
    _log_otel_content_capture_config()
    credentials, project_id = google.auth.default()
    if not project_id:
        logger.warning(
            "GOOGLE_CLOUD_PROJECT not resolved from ADC; trace export may fail"
        )

    service_name = settings.OTEL_SERVICE_NAME
    service_version = settings.commit_sha or settings.APP_VERSION

    base_resource = Resource(
        {
            "service.name": service_name,
            "service.version": service_version,
            "gcp.project_id": project_id or settings.GOOGLE_CLOUD_PROJECT or "",
        }
    )

    try:
        from opentelemetry.resourcedetector.gcp_resource_detector import (
            GoogleCloudResourceDetector,
        )

        resource = base_resource.merge(OTELResourceDetector().detect()).merge(
            GoogleCloudResourceDetector(raise_on_error=False).detect()
        )
    except ImportError:
        logger.warning(
            "opentelemetry-resourcedetector-gcp not installed; using base resource"
        )
        resource = base_resource.merge(OTELResourceDetector().detect())

    session = AuthorizedSession(credentials=credentials)
    exporter = OTLPSpanExporter(
        session=session, endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT
    )

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    logger.info(
        "OTel TracerProvider set → Cloud Trace at %s (project: %s)",
        settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        project_id,
    )

    try:
        from opentelemetry.instrumentation.google_genai import (
            GoogleGenAiSdkInstrumentor,
        )

        GoogleGenAiSdkInstrumentor().instrument()
        logger.info(
            "GoogleGenAiSdkInstrumentor activated (ADK LLM calls will be traced)"
        )
    except ImportError:
        logger.warning("opentelemetry-instrumentation-google-genai not installed")

    _patch_otel_safe_detach()


def flush_telemetry(timeout_millis: int = 30_000) -> bool:
    """Force-export buffered spans without shutting down the TracerProvider."""
    if not settings.OTEL_ENABLED:
        return False
    provider = trace.get_tracer_provider()
    if not hasattr(provider, "force_flush"):
        return False
    try:
        provider.force_flush(timeout_millis=timeout_millis)
        logger.debug("OTel TracerProvider force_flush completed")
        return True
    except Exception as exc:
        logger.warning("OTel TracerProvider force_flush failed: %s", exc)
        return False


def shutdown_telemetry() -> None:
    """Flush in-flight spans before the process exits."""
    flush_telemetry()
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
        logger.info("OTel TracerProvider shut down")
