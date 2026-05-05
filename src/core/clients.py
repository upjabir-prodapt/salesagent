"""Global GCP Client Management (Connection Pooling)"""

from google.cloud import storage, bigquery
from google import genai
from loguru import logger
from ..core.config import settings

class GCPClientPool:
    """Singleton for reusing GCP clients across requests."""
    _storage_client: storage.Client | None = None
    _bq_client: bigquery.Client | None = None
    _genai_client: genai.Client | None = None

    @classmethod
    def get_storage_client(cls) -> storage.Client:
        if cls._storage_client is None:
            logger.info("[Clients] Initializing Global GCS Client")
            cls._storage_client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
        return cls._storage_client

    @classmethod
    def get_bq_client(cls) -> bigquery.Client:
        if cls._bq_client is None:
            logger.info("[Clients] Initializing Global BigQuery Client")
            cls._bq_client = bigquery.Client(project=settings.GOOGLE_CLOUD_PROJECT)
        return cls._bq_client

    @classmethod
    def get_genai_client(cls) -> genai.Client:
        if cls._genai_client is None:
            logger.info("[Clients] Initializing Global Google Gen AI Client")
            cls._genai_client = genai.Client(
                project=settings.GOOGLE_CLOUD_PROJECT,
                location=settings.GOOGLE_CLOUD_LOCATION,
                vertexai=True
            )
        return cls._genai_client

client_pool = GCPClientPool()
