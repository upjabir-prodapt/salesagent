"""Tests for CatalogService job orchestration."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.catalog.service import CatalogService
from src.models.catalog_schemas import CatalogJobOptions


@pytest.fixture
def catalog_service():
    job_repo = MagicMock()
    job_repo.ensure_table_exists.return_value = True
    pipeline = MagicMock()
    pipeline.prepare.return_value = (
        MagicMock(version_id="v1", vector_count=3),
        {"chunks": []},
    )
    pipeline.run.return_value = MagicMock(
        version_id="v1",
        vector_count=3,
        index_vector_count=3,
    )
    return CatalogService(job_repository=job_repo, pipeline=pipeline)


def test_create_job_returns_prefixed_id(catalog_service):
    catalog_service.job_repo.create_job.return_value = True
    with patch(
        "src.services.catalog.service.uuid.uuid4", return_value="abc"
    ):
        job_id = catalog_service.create_job("rebuild", "user@test.com")
    assert job_id.startswith("cat_")


@pytest.mark.asyncio
async def test_process_job_background_rebuild(catalog_service, tmp_path):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 minimal")

    with patch.object(
        catalog_service,
        "require_uploaded_pdf",
        return_value=pdf,
    ):
        await catalog_service.process_job_background(
            "cat_1",
            "rebuild",
            "user@test.com",
            pdf_path=pdf,
            options=CatalogJobOptions(),
        )

    catalog_service.pipeline.run.assert_called_once()
    assert catalog_service.job_repo.update_job.call_count >= 2
