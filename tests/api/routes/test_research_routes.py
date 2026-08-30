def test_root_endpoint(client):
    """Test the root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    assert (
        "Professional Sales Intelligence Research API" in response.json()["description"]
    )


def test_health_endpoint(client):
    """Test the health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_initiate_research_success(client):
    """Test initiating research successfully."""
    payload = {
        "company_name": "Acme Corp",
        "account_id": "0011234567890123",
    }

    client.mock_service.create_research_request.return_value = True

    response = client.post("/api/v1/research/initiate", json=payload)

    assert response.status_code == 202
    data = response.json()
    assert "job_id" in data
    assert data["status"] == "PENDING"

    client.mock_service.create_research_request.assert_called_once()
    client.mock_service.process_research_background.assert_called_once()
    background_call_kwargs = (
        client.mock_service.process_research_background.call_args.kwargs
    )
    assert "trace_context_headers" in background_call_kwargs
    assert isinstance(background_call_kwargs["trace_context_headers"], dict)


def test_get_research_status_success(client):
    """Test getting research status."""
    job_id = "job_123"
    status_data = {
        "request_id": job_id,
        "job_id": job_id,
        "status": "PROCESSING",
        "progress": 50,
        "current_step": "Researching",
    }
    client.mock_service.get_request_status.return_value = status_data

    response = client.get(f"/api/v1/research/status/{job_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "PROCESSING"
    assert data["progress"] == 50
    assert data["request_id"] == job_id
    assert data["job_id"] == job_id


def test_list_research_jobs_success(client):
    """Test listing user's research jobs."""
    jobs_data = [
        {
            "job_id": "job_1",
            "status": "COMPLETED",
            "company_name": "Acme Corp",
            "account_id": "ACC1",
            "progress": 100,
        },
        {
            "job_id": "job_2",
            "status": "PROCESSING",
            "company_name": "Beta Inc",
            "account_id": "ACC2",
            "progress": 40,
        },
    ]
    client.mock_service.list_jobs.return_value = jobs_data

    response = client.get("/api/v1/research/jobs")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 2
    assert items[0]["job_id"] == "job_1"
    assert items[0]["company"] == "Acme Corp"
    assert items[1]["job_id"] == "job_2"


def test_cancel_research_job_success(client):
    """Test cancelling a research job."""
    client.mock_service.cancel_job.return_value = True

    response = client.delete("/api/v1/research/job_123")
    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == "job_123"
    assert data["status"] == "CANCELLED"
    assert data["message"] == "Job cancelled successfully"


def test_get_research_status_not_found(client):
    """Test getting research status for non-existent job."""
    client.mock_service.get_request_status.return_value = None

    response = client.get("/api/v1/research/status/non-existent")
    assert response.status_code == 404


def test_get_research_result_success(client):
    """Test getting research result."""
    job_id = "job_123"
    result_data = {
        "request_id": job_id,
        "status": "COMPLETED",
        "report_content": "# Report",
        "download_url": "http://gcs/report.pdf",
        "model_card": {
            "model_version": "gemini-1.5-pro",
            "tokens_used": 1000,
            "latency_seconds": 10.5,
            "cost_usd": 0.01,
        },
    }
    client.mock_service.get_request_result.return_value = result_data

    response = client.get(f"/api/v1/research/result/{job_id}")

    assert response.status_code == 200
    assert response.json()["request_id"] == job_id


def test_download_pdf_report_success(client):
    """Test downloading PDF report."""
    job_id = "job_123"
    client.mock_service.get_pdf_report.return_value = (b"%PDF-1.4 test", "Acme Corp")

    response = client.get(f"/api/v1/research/download/{job_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "Acme_Corp" in response.headers["content-disposition"]


def test_404_handler(client):
    """Test the custom 404 handler."""
    response = client.get("/non-existent-route")
    assert response.status_code == 404
    assert response.json()["error"] == "NOT_FOUND"


def test_error_handler_middleware(client):
    """Test that the error handler middleware catches unhandled exceptions."""
    client.mock_service.get_request_status.side_effect = Exception("Crash")

    response = client.get("/api/v1/research/status/job_123")
    assert response.status_code == 500
    assert "error" in response.json()
