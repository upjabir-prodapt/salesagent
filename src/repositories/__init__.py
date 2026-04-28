"""Repositories Module - Data access layers."""

from .bigquery_repository import BigQueryRepository
from .gcs_repository import GCSRepository

__all__ = ["BigQueryRepository", "GCSRepository"]
