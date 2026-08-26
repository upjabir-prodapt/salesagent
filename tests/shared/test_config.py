import os
from pathlib import Path
from unittest.mock import patch

import src.shared.config as config


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


def test_resolve_dotenv_path_custom(tmp_path: Path):
    # Must be an OS-absolute path: resolve_dotenv_path() rebases relative
    # values onto the repo root, and "/tmp/..." is not absolute on Windows.
    custom = tmp_path / "custom.env"
    with patch.dict(
        os.environ,
        {"DOTENV_PATH": str(custom), "DOTENV_DISABLE": ""},
        clear=False,
    ):
        assert config.resolve_dotenv_path() == custom


def test_load_dotenv_file_missing_returns_none(tmp_path: Path):
    missing = tmp_path / "missing.env"
    assert config.load_dotenv_file(missing) is None


def test_settings_properties():
    settings = config.settings
    assert settings.bigquery_table_ref.endswith(".research_requests")
    assert settings.gcs_bucket_uri.startswith("gs://")
    assert settings.pricing_catalog_path.name == "pricing_catalog.json"
    assert settings.colt_catalog_path.name == "ColtProductCatalog.pdf"


def test_validate_mounted_assets_success():
    settings = config.settings
    assets = settings.validate_mounted_assets()
    assert "pricing_catalog" in assets
    assert "colt_catalog" in assets
    assert assets["pricing_catalog"].is_file()
    assert assets["colt_catalog"].is_file()


def test_validate_mounted_assets_raises_when_missing(tmp_path: Path):
    import pytest

    with (
        patch.object(config.settings, "ASSETS_ROOT", str(tmp_path / "empty_assets")),
        patch.object(config.settings, "IS_LOCAL", False),
        pytest.raises(RuntimeError, match="Required mounted assets missing"),
    ):
        config.settings.validate_mounted_assets()
