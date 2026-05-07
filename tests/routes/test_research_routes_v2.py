import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import status
from src.core.config import settings

@pytest.fixture
def mock_user():
    return {
        "email": "test@example.com",
        "business_unit": "Sales",
        "organization": "Acme"
    }

def test_initiate_research_success(client, mock_user):
    # Mocking verify_iap_jwt and get_current_user dependencies
    client.mock_service.create_research_request.return_value = True
    client.mock_service.process_research_background = MagicMock()
    
    # Overriding get_current_user in app.dependency_overrides is handled in conftest.py's client fixture
    # but we need to make sure the client fixture we're using mocks the right user.
    from src.dependencies.auth import get_current_user
    client.app.dependency_overrides[get_current_user] = lambda: mock_user

    response = client.post(
        f"{settings.API_PREFIX}/research/initiate",
        json={"company_name": "Acme Corp", "account_id": "ACC123"}
    )
    
    assert response.status_code == status.HTTP_202_ACCEPTED
    data = response.json()
    assert "job_id" in data
    assert data["status"] == "PENDING"
    client.mock_service.create_research_request.assert_called_once()

def test_initiate_research_failure(client, mock_user):
    client.mock_service.create_research_request.return_value = False
    from src.dependencies.auth import get_current_user
    client.app.dependency_overrides[get_current_user] = lambda: mock_user

    response = client.post(
        f"{settings.API_PREFIX}/research/initiate",
        json={"company_name": "Acme Corp", "account_id": "ACC123"}
    )
    
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

def test_get_research_status_success(client):
    client.mock_service.get_request_status.return_value = {
        "request_id": "job_123",
        "status": "PROCESSING",
        "progress": 50,
        "current_step": "Researching"
    }
    
    response = client.get(f"{settings.API_PREFIX}/research/status/job_123")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["progress"] == 50

def test_get_research_status_not_found(client):
    client.mock_service.get_request_status.return_value = None
    response = client.get(f"{settings.API_PREFIX}/research/status/job_none")
    assert response.status_code == status.HTTP_404_NOT_FOUND

def test_get_research_result_success(client):
    client.mock_service.get_request_result.return_value = {
        "request_id": "job_123",
        "status": "COMPLETED",
        "report_content": "# Report",
        "download_url": "http://gcs/report.pdf",
        "model_card": {
            "model_version": "v1",
            "tokens_used": 100,
            "latency_seconds": 1.0,
            "cost_usd": 0.01
        }
    }
    
    response = client.get(f"{settings.API_PREFIX}/research/result/job_123")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "COMPLETED"
    assert data["model_card"]["tokens_used"] == 100

def test_download_pdf_report_success(client):
    client.mock_service.get_pdf_report.return_value = (b"%PDF", "Acme Corp")
    
    response = client.get(f"{settings.API_PREFIX}/research/download/job_123")
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"] == "application/pdf"
    assert "Research_Report_Acme_Corp.pdf" in response.headers["content-disposition"]
