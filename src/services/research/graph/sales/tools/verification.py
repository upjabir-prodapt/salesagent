"""
BM25-based draft verification utilities.

Provides EvidenceStore (accumulates grounding + search evidence from session state)
and Bm25Verifier (scores each factual claim against the corpus via BM25Okapi).

BM25 indices are cached per (session_id, agent_name, evidence_hash) to avoid
rebuilding on repeated verify_draft_answer calls within the same agent loop.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from google.genai import types
from rank_bm25 import BM25Okapi

from .evidence import (
    append_evidence,
    get_agent_evidence,
    normalize_entry,
)

# --- Constants ----------------------------------------------------------------

BM25_MIN_SCORE = 0.8
BM25_MAX_CHUNK_CHARS = 600
BM25_TOKEN_FALLBACK_MIN = 2

GOOGLE_SEARCH_AGENT = "google_search_agent"

PLANNER_TAG_RE = re.compile(r"/\*[A-Z_]+\*/")

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
    "investments are subject to market risks",
    "past performance is not indicative",
    "please conduct your own research",
    "this is not financial advice",
    "for informational purposes",
)

# --- Data types ---------------------------------------------------------------


@dataclass
class VerificationResult:
    status: str
    unsupported: list[str]


# --- Evidence helpers ---------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return [t for t in cleaned.split() if len(t) > 2]


def _chunk_texts(texts: list[str]) -> list[str]:
    """Split long texts into paragraph-sized BM25 documents."""
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
    """Extract all text signals from grounding metadata (titles, URIs, support segments)."""
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


def _entries_from_search_response(
    tool_response: Any, *, agent_name: str = ""
) -> list[dict[str, str]]:
    """Parse structured or plain-text search responses into evidence dicts."""
    entries: list[dict[str, str]] = []

    def add(url: str, title: str, snippet: str) -> None:
        if url or title or snippet:
            entries.append(
                normalize_entry(
                    {"url": url, "title": title, "snippet": snippet},
                    agent_name=agent_name,
                )
            )

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


def claims_from_answer(answer: str) -> list[str]:
    """Split answer into individual checkable claim sentences."""
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


# --- Core classes -------------------------------------------------------------


class EvidenceStore:
    """Accumulates search + grounding text in per-agent session state for BM25."""

    def __init__(self, state: Any, *, agent_name: str | None = None) -> None:
        self._state = state
        self._agent_name = agent_name or ""

    def ingest_grounding(
        self,
        gm: types.GroundingMetadata | None = None,
        *,
        llm_response: Any | None = None,
        agent_name: str | None = None,
    ) -> None:
        """Merge grounding metadata into the agent-scoped evidence corpus."""
        agent = agent_name or self._agent_name
        if not agent:
            return

        resolved = gm
        if resolved is None and llm_response:
            resolved = getattr(llm_response, "grounding_metadata", None)
        if resolved is None:
            raw = self._state.get("temp:_adk_grounding_metadata")
            resolved = raw if isinstance(raw, types.GroundingMetadata) else None
        if resolved is None:
            return

        entries: list[dict] = []
        blobs_key = f"grounding_text_blobs_{agent}"
        sources_key = f"grounding_sources_{agent}"
        sources = list(self._state.get(sources_key) or [])

        for chunk in resolved.grounding_chunks or []:
            if not (chunk.web and chunk.web.uri):
                continue
            uri = chunk.web.uri.strip()
            title = (chunk.web.title or "").strip()
            snippet = title
            rc = chunk.retrieved_context
            if rc and getattr(rc, "text", None):
                snippet = rc.text.strip()
            entries.append(
                normalize_entry(
                    {"url": uri, "title": title, "snippet": snippet, "agent": agent},
                    agent_name=agent,
                )
            )
            sources.append(uri)

        if entries:
            append_evidence(self._state, agent, entries)

        blobs = list(self._state.get(blobs_key) or [])
        blobs.extend(_texts_from_grounding(resolved))
        self._state[blobs_key] = blobs
        self._state[sources_key] = sorted(set(sources))

    def append_search_response(
        self,
        tool_response: Any,
        *,
        source_label: str = GOOGLE_SEARCH_AGENT,
        agent_name: str | None = None,
    ) -> None:
        """Append a search tool response to agent-scoped evidence."""
        agent = agent_name or self._agent_name
        if not agent:
            return

        entries: list[dict] = []
        if isinstance(tool_response, str) and tool_response.strip():
            entries.append(
                normalize_entry(
                    {
                        "url": "",
                        "title": source_label,
                        "snippet": tool_response[:8000],
                        "agent": agent,
                    },
                    agent_name=agent,
                )
            )
        entries.extend(_entries_from_search_response(tool_response, agent_name=agent))
        if entries:
            append_evidence(self._state, agent, entries)
        self.ingest_grounding(None, agent_name=agent)

    def documents(self, agent_name: str | None = None) -> list[str]:
        """Return chunked evidence corpus for BM25 (scoped to one agent)."""
        agent = agent_name or self._agent_name
        texts: list[str] = []
        if agent:
            for item in get_agent_evidence(self._state, agent):
                texts.extend(
                    [
                        item.get("snippet", ""),
                        item.get("title", ""),
                        item.get("url", ""),
                    ]
                )
            blobs_key = f"grounding_text_blobs_{agent}"
            for blob in self._state.get(blobs_key) or []:
                if isinstance(blob, str) and blob.strip():
                    texts.append(blob.strip())
        else:
            for item in self._state.get("search_evidence", []):
                if isinstance(item, dict):
                    texts.extend(
                        [
                            item.get("snippet", ""),
                            item.get("title", ""),
                            item.get("uri", "") or item.get("url", ""),
                        ]
                    )
            for blob in self._state.get("grounding_text_blobs", []):
                if isinstance(blob, str) and blob.strip():
                    texts.append(blob.strip())
        return _chunk_texts([t for t in texts if t])


class Bm25Cache:
    """Per-agent/session BM25 index cache."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[BM25Okapi, set[str]]] = {}

    @staticmethod
    def _hash(docs: list[str]) -> str:
        combined = "\n".join(docs)
        return hashlib.md5(combined.encode(), usedforsecurity=False).hexdigest()

    def get_or_build(
        self,
        docs: list[str],
        *,
        agent_name: str = "unknown",
        session_id: str = "unknown",
    ) -> tuple[BM25Okapi, set[str]]:
        h = self._hash(docs)
        key = f"{session_id}:{agent_name}:{h}"

        if key in self._store:
            return self._store[key]

        prefix = f"{session_id}:{agent_name}:"
        for k in list(self._store):
            if k.startswith(prefix):
                del self._store[k]

        tokenized_docs = [_tokenize(doc) for doc in docs]
        bm25 = BM25Okapi(tokenized_docs)
        global_tokens = set(_tokenize(" ".join(docs)))
        self._store[key] = (bm25, global_tokens)
        return bm25, global_tokens


_bm25_cache = Bm25Cache()


class Bm25Verifier:
    """Scores each factual sentence in a draft against accumulated evidence."""

    def verify(
        self,
        answer: str,
        state: Any,
        *,
        agent_name: str = "unknown",
        session_id: str = "unknown",
    ) -> VerificationResult:
        answer = PLANNER_TAG_RE.sub("", answer).strip()
        docs = EvidenceStore(state, agent_name=agent_name).documents(agent_name)
        if not docs:
            return VerificationResult(
                "FAILED",
                [f"No search evidence found for agent {agent_name}"],
            )

        tokenized_docs = [_tokenize(doc) for doc in docs]
        if not any(tokenized_docs):
            return VerificationResult("FAILED", ["No usable evidence tokens"])

        bm25, global_tokens = _bm25_cache.get_or_build(
            docs, agent_name=agent_name, session_id=session_id
        )

        unsupported: list[str] = []
        checkable = 0

        for claim in claims_from_answer(answer):
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


def compute_semantic_groundedness(
    report: str,
    job_evidence: list[dict],
    *,
    sections: tuple[str, ...] = ("11.", "8."),
) -> float:
    """Compatibility export for semantic groundedness helper."""
    from .embedding_similarity import compute_semantic_groundedness as _impl

    return _impl(report, job_evidence, sections=sections)
