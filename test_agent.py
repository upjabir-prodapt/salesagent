"""
Indian stock market agent: PlanReActPlanner + google_search_agent + BM25 verify.

Run: uv run python test_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.run_config import RunConfig
from google.adk.apps import App
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.models import Gemini, LlmRequest, LlmResponse
from google.adk.planners import PlanReActPlanner
from google.adk.planners.plan_re_act_planner import (
    ACTION_TAG,
    FINAL_ANSWER_TAG,
    PLANNING_TAG,
    REASONING_TAG,
    REPLANNING_TAG,
)
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import google_search
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.google_search_agent_tool import GoogleSearchAgentTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from rank_bm25 import BM25Okapi

from src.core.config import settings
from src.core.model import retry_config

# --- Constants -----------------------------------------------------------------

GOOGLE_SEARCH_AGENT = "google_search_agent"
USER_ID = "test_user"
AGENT_NAME = "StockMarketIndiaResearchAgent"
APP_NAME = "stock_market_india_app"
OUTPUT_KEY = "stock_market_india_output"

BM25_MIN_SCORE = 0.8
BM25_MAX_CHUNK_CHARS = 600
BM25_TOKEN_FALLBACK_MIN = 2
MIN_FINAL_ANSWER_CHARS = 100

PLANNER_TAG_RE = re.compile(r"/\*[A-Z_]+\*/")
TAG_LABELS = {
    PLANNING_TAG: "PLANNING",
    REPLANNING_TAG: "REPLANNING",
    REASONING_TAG: "REASONING",
    ACTION_TAG: "ACTION",
    FINAL_ANSWER_TAG: "FINAL_ANSWER",
}

QUERY_INJECTION_PATTERNS = (
    "ignore previous",
    "ignore all instructions",
    "you are now",
    "disregard your",
    "new instructions:",
    "system prompt",
    "developer message",
    "jailbreak",
)

SKIP_VERIFY_PHRASES = (
    "informational purposes only",
    "does not constitute financial advice",
    "not constitute financial advice",
    "conduct your own research",
    "consult a financial advisor",
    "consult with a qualified financial advisor",
    "following is a summary",
    "summary of these findings",
    "summary of findings",
    "based on the provided web evidence",
    "based on recent web evidence",
    "based on an analysis of recent reports",
    "stock market investments are subject to market risks",
    "past performance is not indicative",
    "please conduct your own research",
)

STOCK_MARKET_INDIA_QUERY = (
    "Which is the best stock to invest in the Indian stock market right now? "
    "Focus on NSE/BSE listed companies. Use only current web evidence from "
    f"{GOOGLE_SEARCH_AGENT}."
)

AGENT_INSTRUCTION = f"""
You are an Indian stock market research assistant (NSE and BSE).
Use {GOOGLE_SEARCH_AGENT} for facts (pass a clear search request). Do not use unstated assumptions.
Every claim must be traceable to retrieved search evidence.

Required workflow (same turn):
1. {PLANNING_TAG} — plan searches.
2. {ACTION_TAG} — call {GOOGLE_SEARCH_AGENT}(request=<search query>) for evidence.
3. {REASONING_TAG} — summarize findings and write a full draft answer.
4. {ACTION_TAG} — call verify_draft_answer(draft=<full draft text>).
5. If verify_draft_answer returns status FAILED: {REPLANNING_TAG}, call {GOOGLE_SEARCH_AGENT}
   again, revise the draft, and call verify_draft_answer again.
6. Only after status PASSED: emit {FINAL_ANSWER_TAG} with the verified draft (no new facts).
"""


# --- Verification --------------------------------------------------------------

@dataclass
class VerificationResult:
    status: str
    unsupported: list[str]


class EvidenceStore:
    """Accumulates search + grounding text in session state for BM25 verification."""

    def __init__(self, state: Any) -> None:
        self._state = state

    def ingest_grounding(
        self,
        gm: types.GroundingMetadata | None = None,
        *,
        llm_response: LlmResponse | None = None,
    ) -> None:
        resolved = gm
        if resolved is None and llm_response:
            resolved = llm_response.grounding_metadata
        if resolved is None:
            raw = self._state.get("temp:_adk_grounding_metadata")
            resolved = raw if isinstance(raw, types.GroundingMetadata) else None
        if resolved is None:
            return
        gm = resolved

        evidence = list(self._state.get("search_evidence", []))
        sources = list(self._state.get("grounding_sources", []))
        blobs = list(self._state.get("grounding_text_blobs", []))

        for chunk in gm.grounding_chunks or []:
            if not (chunk.web and chunk.web.uri):
                continue
            uri = chunk.web.uri.strip()
            title = (chunk.web.title or "").strip()
            snippet = title
            rc = chunk.retrieved_context
            if rc and getattr(rc, "text", None):
                snippet = rc.text.strip()
            evidence.append({"uri": uri, "title": title, "snippet": snippet})
            sources.append(uri)

        blobs.extend(_texts_from_grounding(gm))
        self._state["search_evidence"] = evidence
        self._state["grounding_sources"] = sorted(set(sources))
        self._state["grounding_text_blobs"] = blobs

    def append_search_response(self, tool_response: Any) -> None:
        evidence = list(self._state.get("search_evidence", []))
        if isinstance(tool_response, str) and tool_response.strip():
            evidence.append(
                {
                    "uri": "",
                    "title": GOOGLE_SEARCH_AGENT,
                    "snippet": tool_response[:8000],
                }
            )
        evidence.extend(_entries_from_search_response(tool_response))
        self._state["search_evidence"] = evidence
        self.ingest_grounding(None)

    def documents(self) -> list[str]:
        texts: list[str] = []
        for item in self._state.get("search_evidence", []):
            if isinstance(item, dict):
                texts.extend(
                    [item.get("snippet", ""), item.get("title", ""), item.get("uri", "")]
                )
            elif isinstance(item, str) and item.strip():
                texts.append(item.strip())
        for blob in self._state.get("grounding_text_blobs", []):
            if isinstance(blob, str) and blob.strip():
                texts.append(blob.strip())
        return _chunk_texts([t for t in texts if t])


class Bm25Verifier:
    def verify(self, answer: str, state: Any) -> VerificationResult:
        answer = PLANNER_TAG_RE.sub("", answer).strip()
        docs = EvidenceStore(state).documents()
        if not docs:
            return VerificationResult("FAILED", ["No search evidence found"])

        tokenized_docs = [_tokenize(doc) for doc in docs]
        if not any(tokenized_docs):
            return VerificationResult("FAILED", ["No usable evidence tokens"])

        bm25 = BM25Okapi(tokenized_docs)
        global_tokens = set(_tokenize(" ".join(docs)))
        unsupported: list[str] = []
        checkable = 0

        for claim in _claims_from_answer(answer):
            if len(claim) < 20 or len(_tokenize(claim)) < 3:
                continue
            if _is_boilerplate(claim):
                continue
            checkable += 1
            if not self._claim_supported(bm25, _tokenize(claim), global_tokens):
                unsupported.append(claim)

        if unsupported and checkable >= 5 and len(unsupported) / checkable <= 0.15:
            return VerificationResult("PASSED", unsupported)
        return VerificationResult(
            "PASSED" if not unsupported else "FAILED", unsupported
        )

    @staticmethod
    def _claim_supported(
        bm25: BM25Okapi, query_tokens: list[str], global_tokens: set[str]
    ) -> bool:
        if not query_tokens:
            return False
        max_score = float(max(bm25.get_scores(query_tokens)))
        if max_score >= BM25_MIN_SCORE:
            return True
        if max_score <= 0:
            return len(set(query_tokens) & global_tokens) >= BM25_TOKEN_FALLBACK_MIN
        return False


def _tokenize(text: str) -> list[str]:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return [t for t in cleaned.split() if len(t) > 2]


def _chunk_texts(texts: list[str]) -> list[str]:
    docs: list[str] = []
    for text in texts:
        text = text.strip()
        if not text:
            continue
        if len(text) <= BM25_MAX_CHUNK_CHARS:
            docs.append(text)
            continue
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if len(para) < 40:
                continue
            while len(para) > BM25_MAX_CHUNK_CHARS:
                docs.append(para[:BM25_MAX_CHUNK_CHARS])
                para = para[BM25_MAX_CHUNK_CHARS:].strip()
            if para:
                docs.append(para)
    return docs


def _texts_from_grounding(gm: types.GroundingMetadata) -> list[str]:
    texts: list[str] = []
    for chunk in gm.grounding_chunks or []:
        if chunk.web:
            if chunk.web.title:
                texts.append(chunk.web.title.strip())
            if chunk.web.uri:
                texts.append(chunk.web.uri.strip())
        rc = chunk.retrieved_context
        if rc:
            for field in ("text", "title", "uri", "document_name"):
                val = getattr(rc, field, None)
                if isinstance(val, str) and val.strip():
                    texts.append(val.strip())
    for support in gm.grounding_supports or []:
        seg = support.segment
        if seg and seg.text:
            texts.append(seg.text.strip())
        for part in support.rendered_parts or []:
            if getattr(part, "text", None):
                texts.append(part.text.strip())
    return texts


def _entries_from_search_response(tool_response: Any) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []

    def add(url: str, title: str, snippet: str) -> None:
        if url or title or snippet:
            entries.append({"uri": url, "title": title, "snippet": snippet})

    if isinstance(tool_response, dict):
        for key in ("results", "organic_results", "items"):
            for row in tool_response.get(key) or []:
                if isinstance(row, dict):
                    add(
                        row.get("url") or row.get("link") or "",
                        row.get("title") or "",
                        row.get("snippet") or row.get("description") or "",
                    )
    elif isinstance(tool_response, str):
        for line in tool_response.splitlines():
            if m := re.search(r"https?://\S+", line):
                add(m.group(0), "", line.strip())
    return entries


def _normalize_claim(text: str) -> str:
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    return re.sub(r"^[\*\-]\s*", "", text.strip()).strip()


def _claims_from_answer(answer: str) -> list[str]:
    claims: list[str] = []
    for line in answer.splitlines():
        line = _normalize_claim(line)
        if not line or (line.endswith(":") and len(line.split()) <= 6):
            continue
        for sent in re.split(r"(?<=[.!?])\s+", line):
            if sent := _normalize_claim(sent):
                claims.append(sent)
    return claims


def _is_boilerplate(sentence: str) -> bool:
    low = sentence.lower()
    return any(phrase in low for phrase in SKIP_VERIFY_PHRASES)


def _has_injection(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in QUERY_INJECTION_PATTERNS)


# --- Agent tools & factory -----------------------------------------------------

_verifier = Bm25Verifier()


def verify_draft_answer(draft: str, tool_context: ToolContext) -> dict[str, Any]:
    """Verify draft against session evidence before /*FINAL_ANSWER*/.

    Returns status PASSED/FAILED, unsupported claims, and next-step message.
    """
    EvidenceStore(tool_context.state).ingest_grounding(None)
    result = _verifier.verify(draft, tool_context.state)
    tool_context.state["verification_status"] = result.status
    tool_context.state["unsupported_claims"] = result.unsupported

    if result.status == "PASSED":
        message = "Draft passed evidence check. Emit /*FINAL_ANSWER*/ with this draft only."
    else:
        message = (
            f"Draft failed. Use {REPLANNING_TAG}, call {GOOGLE_SEARCH_AGENT}, "
            "revise, call verify_draft_answer again, then /*FINAL_ANSWER*/."
        )
    return {
        "status": result.status,
        "unsupported": result.unsupported[:8],
        "message": message,
    }


def _research_llm() -> Gemini:
    return Gemini(model=settings.GEMINI_MODEL, http_retry_options=retry_config)


def _google_search_agent_tool() -> GoogleSearchAgentTool:
    search_agent = LlmAgent(
        name=GOOGLE_SEARCH_AGENT,
        model=_research_llm(),
        description="Web search for Indian stock market (NSE/BSE) facts.",
        instruction=(
            "Search the web for Indian equities. Use google_search and return "
            "snippets, URLs, and analyst commentary."
        ),
        tools=[google_search],
    )
    return GoogleSearchAgentTool(search_agent)


def create_app() -> App:
    agent = LlmAgent(
        name=AGENT_NAME,
        model=_research_llm(),
        instruction=AGENT_INSTRUCTION,
        tools=[_google_search_agent_tool(), FunctionTool(verify_draft_answer)],
        planner=PlanReActPlanner(),
        output_key=OUTPUT_KEY,
        before_model_callback=_before_model,
        after_model_callback=_after_model,
        before_tool_callback=_before_tool,
        after_tool_callback=_after_tool,
    )
    return App(name=APP_NAME, root_agent=agent)


# --- Callbacks -----------------------------------------------------------------

def _before_model(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> LlmResponse | None:
    for content in reversed(llm_request.contents or []):
        if getattr(content, "role", None) != "user":
            continue
        message = " ".join(
            p.text for p in (content.parts or []) if getattr(p, "text", None)
        )
        if _has_injection(message):
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text="Blocked potentially injected input in user request."
                        )
                    ],
                )
            )
        break

    if callback_context.state.get("verification_status") == "FAILED":
        bad = callback_context.state.get("unsupported_claims", [])
        llm_request.append_instructions(
            [
                f"verify_draft_answer returned FAILED. Use {REPLANNING_TAG}, "
                f"{GOOGLE_SEARCH_AGENT} for missing facts, revise, call "
                f"verify_draft_answer again, then {FINAL_ANSWER_TAG} only after PASSED. "
                f"Issues: {bad[:3]}"
            ]
        )
    return None


def _after_model(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse | None:
    EvidenceStore(callback_context.state).ingest_grounding(None, llm_response=llm_response)

    text = _visible_answer_text(llm_response)
    if len(text) >= MIN_FINAL_ANSWER_CHARS:
        if callback_context.state.get("verification_status") != "PASSED":
            callback_context.state["verification_status"] = "FAILED"
            callback_context.state["unsupported_claims"] = [
                "FINAL_ANSWER was emitted before verify_draft_answer returned PASSED.",
            ]
    return None


def _before_tool(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> dict[str, Any] | None:
    if tool.name == GOOGLE_SEARCH_AGENT and _has_injection(str(args.get("request", ""))):
        return {"error": "Search request blocked by input policy"}
    return None


def _after_tool(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: Any,
) -> dict[str, Any] | None:
    if tool.name == GOOGLE_SEARCH_AGENT:
        EvidenceStore(tool_context.state).append_search_response(tool_response)
    return None


# --- Streaming / runner --------------------------------------------------------

def _visible_answer_text(llm_response: LlmResponse) -> str:
    content = llm_response.content
    if not content or not content.parts:
        return ""
    return PLANNER_TAG_RE.sub(
        "",
        "\n".join(
            (p.text or "").strip()
            for p in content.parts
            if p.text and not getattr(p, "thought", False)
        ),
    ).strip()


def _phase_label(text: str) -> str:
    for tag, label in TAG_LABELS.items():
        if tag in text:
            return label
    return "TEXT"


def _phase_body(text: str) -> str:
    for tag in TAG_LABELS:
        if tag in text:
            return text[text.find(tag) + len(tag) :].strip()
    return text.strip()


def _print_part(event_num: int, part: types.Part, author: str) -> None:
    if fc := getattr(part, "function_call", None):
        if name := getattr(fc, "name", None):
            args = f" args={fc.args}" if fc.args else ""
            print(f"\n--- Event #{event_num} | ACTION | {author} ---\ntool: {name}{args}\n")
            return
    if fr := getattr(part, "function_response", None):
        if name := getattr(fr, "name", None):
            print(
                f"\n--- Event #{event_num} | TOOL_RESULT | {author} ---\n"
                f"tool: {name} response={fr.response}\n"
            )
            return
    text = (part.text or "").strip()
    if not text:
        return
    kind = "thought" if getattr(part, "thought", False) else "visible"
    print(
        f"\n--- Event #{event_num} | {_phase_label(text)} ({kind}) | {author} ---\n"
        f"{_phase_body(text)}\n"
    )


async def _run_turn(
    runner: Runner,
    app: App,
    session_id: str,
    user_message: types.Content,
    *,
    label: str,
) -> tuple[str, Any]:
    print(f"\n=== Agent stream ({label}) ===\n")
    last_text = ""
    event_num = 0
    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session_id,
        new_message=user_message,
        run_config=RunConfig(),
    ):
        content = getattr(event, "content", None)
        if not content or not content.parts:
            continue
        event_num += 1
        author = str(getattr(event, "author", "agent"))
        for part in content.parts:
            _print_part(event_num, part, author)
            if part.text and not getattr(part, "thought", False):
                last_text = part.text

    session = await runner.session_service.get_session(
        app_name=app.name, user_id=USER_ID, session_id=session_id
    )
    state = session.state if session else {}
    return state.get(OUTPUT_KEY) or last_text, state


async def run_agent() -> tuple[str, Any]:
    app = create_app()
    runner = Runner(
        app=app,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
    )
    session = await runner.session_service.create_session(
        app_name=app.name,
        user_id=USER_ID,
        session_id=f"india_{uuid.uuid4().hex[:8]}",
    )
    message = types.UserContent(parts=[types.Part(text=STOCK_MARKET_INDIA_QUERY)])
    answer, state = await _run_turn(runner, app, session.id, message, label="run")
    print("\n=== End stream ===\n")
    return answer, state


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    print(f"Query: {STOCK_MARKET_INDIA_QUERY}\n")
    answer, state = asyncio.run(run_agent())
    print("--- Final answer ---\n", answer, sep="")
    print("\n--- Verification ---")
    print(f"status: {state.get('verification_status')}")
    print(f"sources: {len(state.get('grounding_sources', []))}")
    if state.get("unsupported_claims"):
        print(f"unsupported: {state.get('unsupported_claims')}")


if __name__ == "__main__":
    main()
