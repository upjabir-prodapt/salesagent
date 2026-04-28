import pytest
from unittest.mock import patch, MagicMock
from src.core.clients import GCPClientPool

def test_get_bq_client(mock_settings):
    with patch("src.core.clients.bigquery.Client") as mock_cls:
        pool = GCPClientPool()
        # Reset singleton state for test
        pool._bq_client = None
        client = pool.get_bq_client()
        mock_cls.assert_called_once()
        assert client is not None

def test_get_storage_client(mock_settings):
    with patch("src.core.clients.storage.Client") as mock_cls:
        pool = GCPClientPool()
        # Reset singleton state for test
        pool._storage_client = None
        client = pool.get_storage_client()
        mock_cls.assert_called_once()
        assert client is not None
