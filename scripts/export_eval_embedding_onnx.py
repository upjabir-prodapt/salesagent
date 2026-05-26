#!/usr/bin/env python3
"""Export EVAL_EMBEDDING_MODEL to ONNX for Section B semantic groundedness.

Requires outbound HTTPS to huggingface.co (or a pre-populated HF cache).

Usage:
    uv run python scripts/export_eval_embedding_onnx.py

Output:
    models/all-MiniLM-L6-v2/onnx/model.onnx
    models/all-MiniLM-L6-v2/  (tokenizer + config for offline load)
"""

from __future__ import annotations

import sys
from pathlib import Path

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = REPO_ROOT / "models" / "all-MiniLM-L6-v2"
ONNX_DIR = MODEL_DIR / "onnx"
ONNX_FILE = ONNX_DIR / "model.onnx"


def main() -> None:
    ONNX_DIR.mkdir(parents=True, exist_ok=True)

    if ONNX_FILE.is_file():
        print(f"ONNX already present: {ONNX_FILE}")
        return

    try:
        from optimum.onnxruntime import ORTModelForFeatureExtraction
    except ImportError as e:
        print(
            "Missing optimum. Install with:\n"
            "  uv add --group dev 'optimum[onnxruntime]'\n"
            "  uv sync --group dev",
            file=sys.stderr,
        )
        raise SystemExit(1) from e

    print(f"Downloading and exporting {MODEL_ID} ...")
    try:
        ort = ORTModelForFeatureExtraction.from_pretrained(MODEL_ID, export=True)
    except Exception as e:
        print(
            "\nExport failed (often network/firewall to huggingface.co).\n"
            "On a machine with HF access, run this script again, then copy:\n"
            f"  {MODEL_DIR}\n"
            "into the repo or Docker image.\n",
            file=sys.stderr,
        )
        raise SystemExit(1) from e

    ort.save_pretrained(str(ONNX_DIR))

    # Tokenizer for OnnxSentenceEmbedder (load from local dir, not HF hub)
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    tokenizer.save_pretrained(str(MODEL_DIR))

    # Ensure expected filename for embedding_similarity.py
    exported = list(ONNX_DIR.glob("*.onnx"))
    if exported and not ONNX_FILE.is_file():
        exported[0].rename(ONNX_FILE)

    if not ONNX_FILE.is_file():
        print(f"Warning: expected {ONNX_FILE} not found; found: {exported}", file=sys.stderr)
        raise SystemExit(1)

    print(f"Exported ONNX to {ONNX_FILE}")
    print(f"Tokenizer saved to {MODEL_DIR}")
    print("Set in .env: EVAL_EMBEDDING_ENABLED=true")


if __name__ == "__main__":
    main()
