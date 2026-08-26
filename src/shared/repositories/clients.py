"""Shared GCP & database client pooling and singletons for repositories."""

from __future__ import annotations

import threading

import redis
import redis.asyncio as aioredis
from google import genai
from google.cloud import bigquery, firestore, storage

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
    """Get shared Google Gen AI client singleton."""
    global _genai_client
    with _lock:
        if _genai_client is None:
            _genai_client = genai.Client()
    return _genai_client


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
                ssl=settings.REDIS_TLS_ENABLED,
                socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT_SECONDS,
                decode_responses=True,
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
                ssl=settings.REDIS_TLS_ENABLED,
                socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT_SECONDS,
                decode_responses=True,
            )
    return _async_redis_client
