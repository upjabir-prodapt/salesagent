"""Shared GCP & database client pooling and singletons for repositories."""

from __future__ import annotations

import threading

import redis
import redis.asyncio as aioredis
from google import genai
from google.cloud import bigquery, firestore, storage
from google.genai import types as genai_types

from ..config import settings

_bq_client: bigquery.Client | None = None
_firestore_client: firestore.Client | None = None
_storage_client: storage.Client | None = None
_genai_client: genai.Client | None = None
_redis_client: redis.Redis | None = None
_async_redis_client: aioredis.Redis | None = None
_lock = threading.Lock()


def get_bigquery_client() -> bigquery.Client:
    """Get shared BigQuery client singleton."""
    global _bq_client
    with _lock:
        if _bq_client is None:
            _bq_client = bigquery.Client(project=settings.GOOGLE_CLOUD_PROJECT)
    return _bq_client


def get_firestore_client() -> firestore.Client:
    """Get shared Firestore client singleton."""
    global _firestore_client
    with _lock:
        if _firestore_client is None:
            _firestore_client = firestore.Client(
                project=settings.GOOGLE_CLOUD_PROJECT,
                database=settings.FIRESTORE_DATABASE,
            )
    return _firestore_client


def get_storage_client() -> storage.Client:
    """Get shared GCS client singleton."""
    global _storage_client
    with _lock:
        if _storage_client is None:
            _storage_client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
    return _storage_client


def get_genai_client() -> genai.Client:
    """Get shared Google Gen AI client singleton.

    Explicitly passes project/location rather than relying on the
    GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION process env vars, so the
    Vertex AI inference region (settings.vertex_ai_location, e.g.
    europe-west3) can differ from the project's infra region
    (settings.GOOGLE_CLOUD_LOCATION, e.g. europe-west1 for Cloud
    Tasks/GCS/BigQuery) without a second process-wide env var.
    """
    global _genai_client
    with _lock:
        if _genai_client is None:
            _genai_client = genai.Client(
                vertexai=settings.GOOGLE_GENAI_USE_VERTEXAI,
                project=settings.GOOGLE_CLOUD_PROJECT,
                location=settings.vertex_ai_location,
                # Belt-and-braces under the app-level asyncio.wait_for
                # deadlines: without this the SDK inherits no socket
                # timeout at all, so a wedged connection can outlive any
                # caller that forgets to bound its own await. Set higher
                # than the per-call app timeouts (e.g.
                # SEARCH_TIMEOUT_SECONDS) so the app-level deadline is
                # normally the one that fires and the caller keeps its
                # own retry semantics. HttpOptions.timeout is in ms.
                http_options=genai_types.HttpOptions(
                    timeout=int(settings.GENAI_HTTP_TIMEOUT_SECONDS * 1000)
                ),
            )
    return _genai_client


def _redis_tls_kwargs() -> dict:
    """TLS kwargs shared by sync/async clients.

    ssl_cert_reqs=None (skip verification) is only used when
    REDIS_TLS_VERIFY_CERT=False -- e.g. Memorystore Redis Cluster over PSC
    presents a Google-managed per-instance CA not in the system trust
    store. Defaults to full verification.
    """
    if not settings.REDIS_TLS_ENABLED:
        return {}
    if not settings.REDIS_TLS_VERIFY_CERT:
        return {"ssl": True, "ssl_cert_reqs": None}
    return {"ssl": True}


def get_redis_client() -> redis.Redis:
    """Get shared sync Redis client singleton."""
    global _redis_client
    with _lock:
        if _redis_client is None:
            _redis_client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD or None,
                db=settings.REDIS_DB,
                socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT_SECONDS,
                decode_responses=True,
                **_redis_tls_kwargs(),
            )
    return _redis_client


def get_async_redis_client() -> aioredis.Redis:
    """Get shared async Redis client singleton."""
    global _async_redis_client
    with _lock:
        if _async_redis_client is None:
            _async_redis_client = aioredis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD or None,
                db=settings.REDIS_DB,
                socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT_SECONDS,
                decode_responses=True,
                **_redis_tls_kwargs(),
            )
    return _async_redis_client
