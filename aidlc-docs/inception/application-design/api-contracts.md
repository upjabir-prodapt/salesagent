# API Contracts & Internal Interfaces

## 1. Public REST API Endpoints (`src/api/`)
- `POST /api/v1/research/initiate`:
  - Request: `{"company_name": str, "account_id": str}`
  - Response: `{"job_id": str, "status": "PENDING", "check_status_url": str}`
- `GET /api/v1/research/status/{job_id}`:
  - Response: `{"request_id": str, "status": str, "progress": int, "current_step": str, "current_agent": str}`
- `GET /api/v1/research/result/{job_id}`:
  - Response: `{"request_id": str, "status": str, "report_content": str, "download_url": str, "model_card": ModelCard}`
- `GET /api/v1/research/download/{job_id}`:
  - Response: Binary PDF stream (`Content-Disposition: attachment; filename="Research_Report_...pdf"`)
- `POST /api/v1/research/{job_id}/feedback`:
  - Request: `{"feedback": {"rating": int, "comments": str}}`
  - Response: `{"job_id": str, "status": "SUCCESS", "message": str}`

## 2. Cloud Tasks Dispatch Endpoint (`src/worker/`)
- `POST /internal/tasks/research`:
  - Auth: Google OIDC Bearer token (`require_cloud_tasks_oidc`)
  - Payload: `ResearchTaskPayload`:
    ```python
    class ResearchTaskPayload(BaseModel):
        job_id: str
        company_name: str
        metadata: dict[str, Any] = Field(default_factory=dict)
        traceparent: str | None = None
        tracestate: str | None = None
    ```
  - Response: `{"job_id": str, "status": str, "action": "ran" | "noop"}`

## 3. ADK Agent State Contracts (`src/worker/domain/agent_contracts.py`)
- `KeywordGeneratorAgent` -> `query_generator_output`
- `ParallelSearchAgent` -> `[firmographicsagent_output, geographicagent_output, executiveagent_output, strategyagent_output, complianceagent_output, marketagent_output, ecosystemagent_output, techstackagent_output, procurementagent_output, growthsignals_output, risksignals_output, campaignsignals_output]`
- `AlignmentAnalyst` -> `alignment_output`
- `ReportCompiler` -> `final_report`
