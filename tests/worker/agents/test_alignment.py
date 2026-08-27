"""Tests for AlignmentAnalyst (src/worker/agents/alignment.py).

Regression test for bug C5: no tool call is used -- the Colt catalog is
injected directly into the rendered prompt, and the step consumes exactly
SearchFindings (no other input).
"""

from __future__ import annotations

import json

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from src.worker.agents.alignment import AlignmentAnalyst
from src.worker.agents.base import RetryPolicy
from src.worker.agents.models import DomainFinding, SearchFindings
from src.worker.observers import Observer


class NullObserver(Observer):
    def on_start(self, agent_name, attempt):
        pass

    def on_retry(self, agent_name, attempt, kind, delay):
        pass

    def on_success(self, agent_name, attempt, seconds):
        pass

    def on_failure(self, agent_name, attempt, kind, exc):
        pass


class ScriptedLlm(BaseLlm):
    model: str = "fake-alignment"
    payload: str = "{}"
    captured_prompts: list = []  # class-level, shared across instances in test

    async def generate_content_async(self, llm_request, stream: bool = False):
        # Capture the rendered prompt text so we can assert on its content.
        text_parts = []
        for content in llm_request.contents or []:
            for part in content.parts or []:
                if getattr(part, "text", None):
                    text_parts.append(part.text)
        ScriptedLlm.captured_prompts.append("\n".join(text_parts))
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.payload)]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=50, candidates_token_count=100
            ),
        )


def _alignment_payload() -> str:
    return json.dumps(
        {
            "alignment_mappings": [
                {
                    "challenge_or_priority": "Legacy WAN limits cloud adoption",
                    "colt_solution": "Colt SD-WAN / SASE",
                    "alignment_justification": "Modernizes connectivity for multi-cloud",
                }
            ],
            "strategic_opportunity": {
                "summary": "Why Colt, why now",
                "hooks": ["Digital transformation urgency"],
            },
        }
    )


def _findings() -> SearchFindings:
    return SearchFindings(
        company="Acme Corp",
        domains={
            "techstackagent_output": DomainFinding(
                domain="tech_stack", content="Acme uses legacy MPLS WAN."
            ),
        },
        executed=1,
        failed=(),
    )


@pytest.mark.asyncio
async def test_alignment_analyst_produces_typed_alignment():
    ScriptedLlm.captured_prompts = []
    analyst = AlignmentAnalyst(retry=RetryPolicy(max_attempts=1))
    original_build_agent = analyst.build_agent

    def build_agent_with_fake_llm():
        agent = original_build_agent()
        agent.model = ScriptedLlm(payload=_alignment_payload())
        return agent

    analyst.build_agent = build_agent_with_fake_llm  # type: ignore[method-assign]

    result = await analyst.run(_findings(), NullObserver())

    assert len(result.mappings) == 1
    assert result.mappings[0].solution == "Colt SD-WAN / SASE"
    assert result.opportunity_summary == "Why Colt, why now"
    assert result.hooks == ("Digital transformation urgency",)


@pytest.mark.asyncio
async def test_alignment_analyst_prompt_contains_no_tool_reference():
    """Regression test for C5: the rendered prompt must not instruct the
    model to call any retrieval tool -- the catalog is already inline.
    """
    ScriptedLlm.captured_prompts = []
    analyst = AlignmentAnalyst(retry=RetryPolicy(max_attempts=1))
    original_build_agent = analyst.build_agent

    def build_agent_with_fake_llm():
        agent = original_build_agent()
        agent.model = ScriptedLlm(payload=_alignment_payload())
        return agent

    analyst.build_agent = build_agent_with_fake_llm  # type: ignore[method-assign]

    await analyst.run(_findings(), NullObserver())

    assert len(ScriptedLlm.captured_prompts) == 1
    prompt_text = ScriptedLlm.captured_prompts[0]
    assert "retrieve_alignment_context" not in prompt_text
    assert "Acme uses legacy MPLS WAN." in prompt_text


@pytest.mark.asyncio
async def test_alignment_analyst_has_no_tools_registered():
    analyst = AlignmentAnalyst()
    agent = analyst.build_agent()
    assert agent.tools == []
