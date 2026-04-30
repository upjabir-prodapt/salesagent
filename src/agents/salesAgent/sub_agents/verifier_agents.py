from google.adk.agents import LlmAgent
from google.adk.tools import google_search

VERIFIER_INSTRUCTION = """
You are a fact-checking agent. You receive:
1. A claim extracted from a research section
2. A source URL where that claim was found

Your ONLY job:
- Use google_search to retrieve content from that source URL or corroborating sources
- Determine whether the claim is directly supported by what you find
- Do NOT use your training knowledge — base verdict only on retrieved content

Return ONLY valid JSON:
{
  "verdict": "SUPPORTED" | "REFUTED" | "UNVERIFIABLE",
  "evidence": "<one sentence quoting or summarising the retrieved source>",
  "source_checked": "<URL you verified against>"
}
"""

def create_verifier_agent(agent_name: str) -> LlmAgent:
    """Create a specialized fact-checking agent for a specific research agent."""
    return LlmAgent(
        name=f"{agent_name}_verifier",
        model="gemini-2.5-flash",
        instruction=VERIFIER_INSTRUCTION,
        tools=[google_search],
        output_key=f"{agent_name}_verification_result",
    )
