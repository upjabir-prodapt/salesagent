"""Shared session / job ID helpers for API, ADK, and GCS."""


def runner_session_id(job_id: str, attempt: int = 0) -> str:
    """ADK session id for a research job run.

    Attempt 0 uses the API job_id directly. Higher attempts are reserved for
    optional outer backstop retries (``{job_id}_retry_{n}``).
    """
    if attempt <= 0:
        return job_id
    return f"{job_id}_retry_{attempt}"
