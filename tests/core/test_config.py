import os
from pathlib import Path
from unittest.mock import patch

import src.core.config as config


def test_is_local_runtime_explicit_true():
    with patch.dict(os.environ, {"IS_LOCAL": "true"}, clear=False):
        assert config.is_local_runtime() is True


def test_is_local_runtime_explicit_false():
    with patch.dict(os.environ, {"IS_LOCAL": "false"}, clear=False):
        assert config.is_local_runtime() is False


def test_resolve_dotenv_path_cloud_run():
    with patch.dict(
        os.environ,
        {"IS_LOCAL": "false", "DOTENV_DISABLE": "", "DOTENV_PATH": ""},
        clear=False,
    ):
        assert config.resolve_dotenv_path() == config.CLOUD_RUN_ENV_FILE


def test_resolve_dotenv_path_custom():
    custom = "/tmp/custom.env"
    with patch.dict(
        os.environ,
        {"DOTENV_PATH": custom, "DOTENV_DISABLE": ""},
        clear=False,
    ):
        assert config.resolve_dotenv_path() == Path(custom)


def test_load_dotenv_file_missing_returns_none(tmp_path: Path):
    missing = tmp_path / "missing.env"
    assert config.load_dotenv_file(missing) is None


def test_settings_properties():
    settings = config.settings
    assert settings.bigquery_table_ref.endswith(".research_requests")
    assert settings.gcs_bucket_uri.startswith("gs://")
    assert (
        settings.BIGQUERY_CATALOG_JOBS_TABLE in settings.bigquery_catalog_jobs_table_ref
    )
