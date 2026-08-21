"""Tool for providing alignment context from PDF."""

from __future__ import annotations

from typing import Any

from google.adk.tools.function_tool import FunctionTool

from .gcs_pdf_loader import get_alignment_context


def get_context_tool_fn(company_name: str) -> Any:
    """Create a function that returns alignment context."""

    def retrieve_alignment_context() -> dict[str, Any]:
        """Retrieve Colt alignment context from PDF or hardcoded fallback."""
        context = get_alignment_context(company_name)
        return {
            "status": "SUCCESS",
            "context": context,
            "context_length": len(context),
            "message": "Colt product catalog context loaded successfully",
        }

    return retrieve_alignment_context


def make_alignment_context_tool(company_name: str) -> FunctionTool:
    """Create tool for retrieving alignment context."""
    context_fn = get_context_tool_fn(company_name)
    return FunctionTool(context_fn)


__all__ = ["make_alignment_context_tool", "get_context_tool_fn"]
