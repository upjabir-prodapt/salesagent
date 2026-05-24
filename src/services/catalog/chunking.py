"""PDF extraction and text chunking."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from ...core.config import Settings


@dataclass(frozen=True)
class TextChunk:
    chunk_id: str
    text: str
    index: int


@dataclass(frozen=True)
class ChunkingResult:
    source_path: Path
    full_text: str
    chunks: list[TextChunk]


def extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    parts = [(page.extract_text() or "").strip() for page in reader.pages]
    text = "\n\n".join(p for p in parts if p)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_text(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    if not text:
        return []
    pieces: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk_size)
        if end < n:
            space = text.rfind(" ", start, end)
            if space > start + chunk_size // 3:
                end = space
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= n:
            break
        start = max(start + 1, end - overlap)
    return pieces


def chunk_pdf(settings: Settings, pdf_path: Path) -> ChunkingResult:
    text = extract_pdf_text(pdf_path)
    raw_chunks = split_text(
        text,
        chunk_size=settings.VECTOR_SEARCH_CHUNK_SIZE,
        overlap=settings.VECTOR_SEARCH_CHUNK_OVERLAP,
    )
    if not raw_chunks:
        raise ValueError(f"No text extracted from {pdf_path}")

    chunks = [
        TextChunk(
            chunk_id=f"{settings.VECTOR_SEARCH_CHUNK_ID_PREFIX}{i:04d}",
            text=body,
            index=i,
        )
        for i, body in enumerate(raw_chunks)
    ]
    return ChunkingResult(source_path=pdf_path, full_text=text, chunks=chunks)
