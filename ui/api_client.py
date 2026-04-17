import os
from typing import Any

import requests
from loguru import logger

# Base URL for the FastAPI backend. Default to localhost if not specified.
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")


class APIClient:
    """Client for interacting with the Colt-AI FastAPI backend."""

    @staticmethod
    def submit_research(company_name: str) -> dict[str, Any]:
        """Submit a new company for research."""
        endpoint = f"{API_BASE_URL}/research/ingest"
        payload = {"company_name": company_name, "metadata": {"source": "streamlit_ui"}}
        try:
            response = requests.post(endpoint, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error submitting research for {company_name}: {e}")
            if hasattr(e, "response") and e.response is not None:
                try:
                    return {
                        "error": True,
                        "detail": e.response.json().get("detail", str(e)),
                    }
                except Exception:
                    pass
            return {"error": True, "detail": str(e)}

    @staticmethod
    def check_status(request_id: str) -> dict[str, Any]:
        """Check the status of an ongoing research request."""
        endpoint = f"{API_BASE_URL}/research/status/{request_id}"
        try:
            response = requests.get(endpoint)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error checking status for {request_id}: {e}")
            return {"error": True, "detail": str(e)}

    @staticmethod
    def get_result(request_id: str, include_content: bool = True) -> dict[str, Any]:
        """Get the final result of a completed research request."""
        endpoint = f"{API_BASE_URL}/research/result/{request_id}?include_content={str(include_content).lower()}"
        try:
            response = requests.get(endpoint)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting result for {request_id}: {e}")
            return {"error": True, "detail": str(e)}
