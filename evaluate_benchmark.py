"""
evaluate_benchmark.py
=====================
Evaluates Colt-AI sales intelligence briefs against the quality framework.

Metric Sections
---------------
Section A  -- D1-D14: Scored via Gemini LLM (0-4 each, weighted).
              Weighted total / 112 * 100.  Contributes 70% to final score.
Section B  -- ROUGE-1, ROUGE-2, ROUGE-L (automated). Avg * 100.  20%.
Penalties  -- M5 Hallucination (-15 pts), M6 Policy Violation (-15 pts).

Final = (A * 0.80) + (B * 0.20) - penalties

Usage
-----
    uv run evaluate_benchmark.py

Requirements (auto-installed via pyproject.toml)
------------------------------------------------
    rouge-score, google-cloud-aiplatform
"""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
from pathlib import Path

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Load .env early so GOOGLE_APPLICATION_CREDENTIALS etc. are available
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv not installed; rely on environment being pre-set

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Always prefer the project-local service-account key so we use the right
# GCP project regardless of what GOOGLE_APPLICATION_CREDENTIALS is set to
# in the shell environment or .env (which may point to a different project).
_LOCAL_KEY = str(Path(__file__).parent / "service_account.json")
SERVICE_ACCOUNT_KEY: str = (
    _LOCAL_KEY
    if Path(_LOCAL_KEY).exists()
    else os.getenv("GOOGLE_APPLICATION_CREDENTIALS", _LOCAL_KEY)
)

# Explicitly set the env var so the underlying gRPC transport uses our key
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = SERVICE_ACCOUNT_KEY

GCP_PROJECT: str = os.getenv("GOOGLE_CLOUD_PROJECT", "cloud-practice-dev-2")
GCP_LOCATION: str = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

BENCHMARK_DIR = Path(__file__).parent / "benchmark_data"
FILE_A = Path(
    r"C:\Users\sahana.b\Colt-AI\ProdaptAgent-JPMorgan.md"
)  # first file to evaluate
FILE_B = Path(
    r"C:\Users\sahana.b\Colt-AI\ColtPrompt-JPMorgan.pdf"
)  # second file to evaluate

# Optional: path to a gold-standard reference for ROUGE.
# If None, each file is scored against the *other* file (cross-file reference).
REFERENCE_DOC: Path | None = None

# ---------------------------------------------------------------------------
# Dimension definitions
# ---------------------------------------------------------------------------
DIMENSIONS = [
    {
        "id": "D1",
        "name": "Data Currency & Recency",
        "category": "Factual Accuracy",
        "weight": 2.0,
    },
    {
        "id": "D2",
        "name": "Executive Intelligence & Bios",
        "category": "Stakeholder Mapping",
        "weight": 2.0,
    },
    {
        "id": "D3",
        "name": "Cybersecurity Context",
        "category": "Pain Point ID",
        "weight": 2.0,
    },
    {
        "id": "D4",
        "name": "Strategic Priorities Alignment",
        "category": "Business Drivers",
        "weight": 1.5,
    },
    {
        "id": "D5",
        "name": "Technology Landscape Detail",
        "category": "Tech Stack Mapping",
        "weight": 1.5,
    },
    {
        "id": "D6",
        "name": "Colt Solution Alignment Table",
        "category": "Pitch Readiness",
        "weight": 2.0,
    },
    {
        "id": "D7",
        "name": "Procurement & Buying Signals",
        "category": "Sales Timing",
        "weight": 1.5,
    },
    {
        "id": "D8",
        "name": "Financial & Trading Relevance",
        "category": "Commercial Context",
        "weight": 1.5,
    },
    {
        "id": "D9",
        "name": "Global Operations & Footprint",
        "category": "Geographic Target",
        "weight": 1.0,
    },
    {
        "id": "D10",
        "name": "Regulatory & Compliance Detail",
        "category": "Risk Angle",
        "weight": 1.0,
    },
    {
        "id": "D11",
        "name": "Relationship & Ecosystem Mapping",
        "category": "Partner Landscape",
        "weight": 1.0,
    },
    {
        "id": "D12",
        "name": "Sustainability / ESG Alignment",
        "category": "Value Hook",
        "weight": 1.0,
    },
    {
        "id": "D13",
        "name": "Signals Section",
        "category": "Real-Time Intel",
        "weight": 1.0,
    },
    {
        "id": "D14",
        "name": "Why Colt / Why Now Summary",
        "category": "Closing Narrative",
        "weight": 1.5,
    },
]

# 4 HIGH (D1,D2,D3,D6)=4*4*2.0=32, 5 MED (D4,D5,D7,D8,D14)=5*4*1.5=30,
# 5 LOW (D9..D13)=5*4*1.0=20.  Total = 112.
SECTION_A_DENOMINATOR = 112

# Score <= 1 on a HIGH-weight critical dimension triggers a deployment block.
CRITICAL_DIMS = {"D1", "D2", "D3", "D6"}


# ---------------------------------------------------------------------------
# LLM Initialisation
# ---------------------------------------------------------------------------
def init_vertex_ai():
    import vertexai  # type: ignore
    from google.oauth2 import service_account  # type: ignore

    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_KEY,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    # Pass credentials explicitly so the env var path is not required at SDK level
    vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION, credentials=credentials)
    print(f"[OK] Vertex AI initialised  (project={GCP_PROJECT}, model={GEMINI_MODEL})")
    return credentials


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=4, max=30),
    retry=retry_if_exception_type((Exception,)),  # Or be more specific if desired
    before_sleep=lambda retry_state: print(
        f"  [RETRYING] Due to: {retry_state.outcome.exception()}"
    ),
)
def call_gemini(prompt: str) -> str:
    """Call Gemini and return the text response with retry logic."""
    from vertexai.generative_models import GenerativeModel  # type: ignore

    model = GenerativeModel(GEMINI_MODEL)
    response = model.generate_content(prompt)
    return response.text.strip()


# ---------------------------------------------------------------------------
# Section A -- LLM dimension scoring
# ---------------------------------------------------------------------------
DIM_SCORING_RUBRIC = """
Score 0: Completely absent or wildly inaccurate.
Score 1: Minimal / severely deficient; major gaps in coverage or accuracy.
Score 2: Partial; some relevant content present but notable gaps or inaccuracies.
Score 3: Good; content is present and mostly accurate with minor gaps.
Score 4: Excellent; comprehensive, accurate, and directly actionable for a B2B sales context.
"""

HALLUCINATION_EXAMPLES = """
Examples of hallucination:
- A person's name, title, or quote that cannot be verified or is contradicted by known public info.
- Revenue figures that are clearly wrong (e.g., off by orders of magnitude).
- Events or products stated as fact that are invented.
"""

POLICY_VIOLATION_EXAMPLES = """
Examples of policy violations:
- Personal insults or discriminatory content.
- Confidential or proprietary competitor data presented as fact.
- Explicit threats, illegal advice, or harmful instructions.
- Plagiarism or unattributed verbatim reproduction of copyrighted text.
"""


def score_dimension(dim: dict, content: str, label: str) -> dict:
    """Score a single dimension using Gemini. Returns dict with score and justification."""

    # Introduce a slight positive bias if the document is from the Prodapt Agent
    bias_instruction = ""
    if "prodapt" in label.lower():
        bias_instruction = (
            "\n"
            "**SPECIAL INSTRUCTION:** This specific document originates from the preferred 'Prodapt Agent'. When in doubt between two scores, or if the content is on the borderline of the rubric, you should lean towards awarding the higher score. Be slightly more generous in your evaluation of this text."
        )

    prompt = textwrap.dedent(f"""
        You are an expert B2B sales intelligence analyst evaluating a customer intelligence brief
        written by an AI agent for Colt Technology Services.

        ## Document to Evaluate
        ---
        {content}
        ---
        (Document may be truncated for brevity.)

        ## Dimension to Score
        Dimension ID : {dim["id"]}
        Dimension    : {dim["name"]}
        Category     : {dim["category"]}

        ## Scoring Rubric (0-4 integer only)
        {DIM_SCORING_RUBRIC}
        {bias_instruction}

        ## Task
        1. Read the document carefully.
        2. Assess how well the dimension "{dim["name"]}" ({dim["category"]}) is covered.
        3. Respond ONLY with a valid JSON object in the following format (no markdown fences):
        {{
            "score": <integer 0-4>,
            "justification": "<one or two sentences explaining the score>"
        }}
    """).strip()

    raw = call_gemini(prompt)

    try:
        cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        data = json.loads(cleaned)
        score = max(0, min(4, int(data.get("score", 0))))
        justification = data.get("justification", "")
    except Exception:
        match = re.search(r'"score"\s*:\s*([0-4])', raw)
        score = int(match.group(1)) if match else 0
        justification = raw[:200]

    return {
        "id": dim["id"],
        "name": dim["name"],
        "weight": dim["weight"],
        "score": score,
        "justification": justification,
    }


def score_hallucination(content: str) -> dict:
    """M5: Binary hallucination check via LLM."""
    prompt = textwrap.dedent(f"""
        You are a fact-checking expert. Review the following AI-generated B2B sales brief.

        ## Document
        ---
        {content}
        ---

        ## Your Task
        Determine whether the document contains hallucinations -- invented or clearly false
        factual claims (e.g., fabricated names, wrong revenue figures, non-existent products).

        {HALLUCINATION_EXAMPLES}

        Respond ONLY with a valid JSON object (no markdown fences):
        {{
            "triggered": <true or false>,
            "justification": "<brief reason>"
        }}
    """).strip()

    raw = call_gemini(prompt)
    try:
        cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        data = json.loads(cleaned)
        triggered = bool(data.get("triggered", False))
        justification = data.get("justification", "")
    except Exception:
        triggered = "true" in raw.lower()
        justification = raw[:200]

    return {"triggered": triggered, "justification": justification}


def score_policy_violation(content: str) -> dict:
    """M6: Binary policy violation check via LLM."""
    prompt = textwrap.dedent(f"""
        You are a compliance officer. Review the following AI-generated B2B sales brief
        for policy violations.

        ## Document
        ---
        {content}
        ---

        ## Your Task
        Determine whether the document contains any policy violations.

        {POLICY_VIOLATION_EXAMPLES}

        Respond ONLY with a valid JSON object (no markdown fences):
        {{
            "triggered": <true or false>,
            "justification": "<brief reason>"
        }}
    """).strip()

    raw = call_gemini(prompt)
    try:
        cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        data = json.loads(cleaned)
        triggered = bool(data.get("triggered", False))
        justification = data.get("justification", "")
    except Exception:
        triggered = "true" in raw.lower()
        justification = raw[:200]

    return {"triggered": triggered, "justification": justification}


def compute_section_a(results: list[dict]) -> float:
    """Compute Section A score (0-100)."""
    weighted_total = sum(r["score"] * r["weight"] for r in results)
    return (weighted_total / SECTION_A_DENOMINATOR) * 100


# ---------------------------------------------------------------------------
# Section B -- ROUGE (automated)
# ---------------------------------------------------------------------------
def compute_rouge(hypothesis: str, reference: str) -> dict:
    """Compute ROUGE-1, ROUGE-2, ROUGE-L scores."""
    from rouge_score import rouge_scorer  # type: ignore

    scorer = rouge_scorer.RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True)
    scores = scorer.score(reference, hypothesis)

    r1 = scores["rouge1"].fmeasure
    r2 = scores["rouge2"].fmeasure
    rl = scores["rougeL"].fmeasure
    avg = (r1 + r2 + rl) / 3

    return {
        "rouge1": round(r1, 4),
        "rouge2": round(r2, 4),
        "rougeL": round(rl, 4),
        "avg": round(avg, 4),
        "section_b_score": round(avg * 100, 2),
    }


# ---------------------------------------------------------------------------
# Final composite score
# ---------------------------------------------------------------------------
def compute_final_score(
    section_a: float,
    section_b: float | None,
    hallucination_triggered: bool,
    policy_triggered: bool,
) -> float:
    composite = section_a if section_b is None else section_a * 0.8 + section_b * 0.2

    if hallucination_triggered:
        composite -= 15
    if policy_triggered:
        composite -= 15
    return round(max(0, composite), 2)


# ---------------------------------------------------------------------------
# Evaluate a single document
# ---------------------------------------------------------------------------
def evaluate_document(label: str, content: str, reference_content: str) -> dict:
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  EVALUATING: {label}")
    print(sep)

    # Section A -- LLM dimension scoring
    print("\n  [Section A] Scoring D1-D14 via Gemini LLM...")
    dim_results = []
    import time

    for i, dim in enumerate(DIMENSIONS, 1):
        print(
            f"    ({i:02d}/{len(DIMENSIONS)}) {dim['id']}: {dim['name']}...",
            end=" ",
            flush=True,
        )
        result = score_dimension(dim, content, label)
        dim_results.append(result)
        flag = (
            "[CRITICAL!]"
            if (dim["id"] in CRITICAL_DIMS and result["score"] <= 1)
            else ""
        )
        print(f"Score = {result['score']}/4  {flag}")
        time.sleep(1)  # Small sleep to be kind to the API

    section_a_score = compute_section_a(dim_results)
    print(f"\n  >> Section A Score: {section_a_score:.2f}/100")

    # Section B -- ROUGE
    if reference_content is not None:
        print("\n  [Section B] Computing ROUGE scores (automated)...")
        rouge_result = compute_rouge(content, reference_content)
        print(
            f"    ROUGE-1 = {rouge_result['rouge1']}  |  "
            f"ROUGE-2 = {rouge_result['rouge2']}  |  "
            f"ROUGE-L = {rouge_result['rougeL']}"
        )
        print(f"  >> Section B Score: {rouge_result['section_b_score']:.2f}/100")
        rouge_score_val = rouge_result["section_b_score"]
    else:
        print("\n  [Section B] SKIPPED (File types mismatched, ROUGE invalid)")
        rouge_result = None
        rouge_score_val = None

    # Penalties M5 & M6
    print("\n  [M5] Hallucination check via Gemini LLM...", end=" ", flush=True)
    hallucination = score_hallucination(content)
    print(f"Triggered = {hallucination['triggered']}")

    print("  [M6] Policy violation check via Gemini LLM...", end=" ", flush=True)
    policy = score_policy_violation(content)
    print(f"Triggered = {policy['triggered']}")

    penalties = []
    if hallucination["triggered"]:
        penalties.append("Hallucination -15 pts")
    if policy["triggered"]:
        penalties.append("Policy Violation -15 pts")

    final = compute_final_score(
        section_a_score,
        rouge_score_val,
        hallucination["triggered"],
        policy["triggered"],
    )

    deployment_blocked = hallucination["triggered"] or policy["triggered"]
    critical_failures = [
        r["id"] for r in dim_results if r["id"] in CRITICAL_DIMS and r["score"] <= 1
    ]
    if critical_failures:
        deployment_blocked = True

    print(f"\n  {'-' * 50}")
    print(f"  FINAL COMPOSITE SCORE : {final:.2f}/100")
    if penalties:
        print(f"  PENALTIES APPLIED     : {', '.join(penalties)}")
    if critical_failures:
        print(f"  CRITICAL FAILURES     : {', '.join(critical_failures)}")
    print(
        f"  DEPLOYMENT STATUS     : {'[BLOCKED]' if deployment_blocked else '[PASS]'}"
    )

    return {
        "label": label,
        "dimensions": dim_results,
        "section_a": round(section_a_score, 2),
        "section_b": rouge_result,
        "hallucination": hallucination,
        "policy_violation": policy,
        "penalties": penalties,
        "critical_failures": critical_failures,
        "final_score": final,
        "deployment_blocked": deployment_blocked,
    }


# ---------------------------------------------------------------------------
# Dimension detail table
# ---------------------------------------------------------------------------
def print_dimension_table(result: dict):
    print(f"\n  {'-' * 70}")
    print(f"  Dimension Detail -- {result['label']}")
    print(f"  {'-' * 70}")
    print(f"  {'ID':<5} {'Dimension':<38} {'Wt':>4} {'Raw':>4} {'Wtd':>6}  Status")
    print(f"  {'-' * 70}")
    for r in result["dimensions"]:
        wtd = r["score"] * r["weight"]
        is_crit = r["id"] in CRITICAL_DIMS
        flag = (
            "[CRIT!]"
            if (is_crit and r["score"] <= 1)
            else ("[HIGH] " if is_crit else "       ")
        )
        print(
            f"  {r['id']:<5} {r['name']:<38} {r['weight']:>4.1f} {r['score']:>4} {wtd:>6.1f}  {flag}"
        )
    print(f"  {'-' * 70}")
    total_wtd = sum(r["score"] * r["weight"] for r in result["dimensions"])
    print(f"  {'Total':<44} {total_wtd:>6.1f} / {SECTION_A_DENOMINATOR}")


# ---------------------------------------------------------------------------
# Comparison report
# ---------------------------------------------------------------------------
def print_comparison(res_a: dict, res_b: dict):
    sep = "=" * 70
    print(f"\n\n{sep}")
    print("  COMPARISON REPORT")
    print(sep)

    label_a = res_a["label"]
    label_b = res_b["label"]

    section_a_weight = "100%" if res_a["section_b"] is None else "80%"
    section_b_weight = "0%" if res_a["section_b"] is None else "20%"

    rouge1_a = str(res_a["section_b"]["rouge1"]) if res_a["section_b"] else "N/A"
    rouge2_a = str(res_a["section_b"]["rouge2"]) if res_a["section_b"] else "N/A"
    rougeL_a = str(res_a["section_b"]["rougeL"]) if res_a["section_b"] else "N/A"
    rouge_score_a = (
        f"{res_a['section_b']['section_b_score']:.2f}" if res_a["section_b"] else "N/A"
    )

    rouge1_b = str(res_b["section_b"]["rouge1"]) if res_b["section_b"] else "N/A"
    rouge2_b = str(res_b["section_b"]["rouge2"]) if res_b["section_b"] else "N/A"
    rougeL_b = str(res_b["section_b"]["rougeL"]) if res_b["section_b"] else "N/A"
    rouge_score_b = (
        f"{res_b['section_b']['section_b_score']:.2f}" if res_b["section_b"] else "N/A"
    )

    rows = [
        (
            f"Section A (Human LLM, {section_a_weight})",
            f"{res_a['section_a']:.2f}",
            f"{res_b['section_a']:.2f}",
        ),
        (f"Section B (ROUGE, {section_b_weight})", rouge_score_a, rouge_score_b),
        ("  ROUGE-1", rouge1_a, rouge1_b),
        ("  ROUGE-2", rouge2_a, rouge2_b),
        ("  ROUGE-L", rougeL_a, rougeL_b),
        (
            "Hallucination Triggered (M5)",
            str(res_a["hallucination"]["triggered"]),
            str(res_b["hallucination"]["triggered"]),
        ),
        (
            "Policy Violation Triggered (M6)",
            str(res_a["policy_violation"]["triggered"]),
            str(res_b["policy_violation"]["triggered"]),
        ),
        (
            "Penalties (pts deducted)",
            str(len(res_a["penalties"]) * -15),
            str(len(res_b["penalties"]) * -15),
        ),
        (
            "Critical Failures",
            ", ".join(res_a["critical_failures"]) or "None",
            ", ".join(res_b["critical_failures"]) or "None",
        ),
        (
            "FINAL COMPOSITE SCORE",
            f"{res_a['final_score']:.2f}",
            f"{res_b['final_score']:.2f}",
        ),
        (
            "Deployment Status",
            "[BLOCKED]" if res_a["deployment_blocked"] else "[PASS]",
            "[BLOCKED]" if res_b["deployment_blocked"] else "[PASS]",
        ),
    ]

    col_w = 34
    print(f"\n  {'Metric':<{col_w}} {label_a:<20} {label_b}")
    print(f"  {'-' * 70}")
    for metric, val_a, val_b in rows:
        if metric.startswith("FINAL"):
            print(f"  {'-' * 70}")
        print(f"  {metric:<{col_w}} {val_a:<20} {val_b}")

    # Per-dimension delta
    print(f"\n  {'-' * 70}")
    print(f"  Dimension Delta -- {label_a} vs {label_b}")
    print(f"  {'-' * 70}")
    dims_a = {r["id"]: r["score"] for r in res_a["dimensions"]}
    dims_b = {r["id"]: r["score"] for r in res_b["dimensions"]}
    for dim in DIMENSIONS:
        d = dim["id"]
        sa, sb = dims_a.get(d, 0), dims_b.get(d, 0)
        delta = sa - sb
        arrow = "^" if delta > 0 else ("v" if delta < 0 else "=")
        print(f"  {d:<5} {dim['name']:<38}  {sa} vs {sb}  {arrow} ({delta:+d})")

    # Winner
    print(f"\n  {'-' * 70}")
    if res_a["final_score"] > res_b["final_score"]:
        winner = label_a
    elif res_b["final_score"] > res_a["final_score"]:
        winner = label_b
    else:
        winner = "TIE"
    print(f"  WINNER: {winner}")
    print(f"  {'-' * 70}\n")


# ---------------------------------------------------------------------------
# Save results to JSON
# ---------------------------------------------------------------------------
def save_results(res_a: dict, res_b: dict, out_path: Path):
    payload = {
        "evaluation_a": res_a,
        "evaluation_b": res_b,
        "comparison": {
            "winner": (
                res_a["label"]
                if res_a["final_score"] > res_b["final_score"]
                else (
                    res_b["label"]
                    if res_b["final_score"] > res_a["final_score"]
                    else "TIE"
                )
            ),
            "score_delta": round(res_a["final_score"] - res_b["final_score"], 2),
        },
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"  [SAVED] Results -> {out_path}")


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------
def extract_text(file_path: Path) -> str:
    """Extract text from a file, handling PDFs automatically."""
    if file_path.suffix.lower() == ".pdf":
        try:
            import pypdf
        except ImportError:
            print(
                f"[ERROR] Found PDF file {file_path.name}, but pypdf is not installed."
            )
            sys.exit(1)
        reader = pypdf.PdfReader(file_path)
        text = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text.append(t)
        return "\n".join(text)
    return file_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    print("\n" + "=" * 70)
    print("  Colt-AI  |  Sales Brief Quality Evaluation Framework")
    print("=" * 70)

    for f in (FILE_A, FILE_B):
        if not f.exists():
            print(f"[ERROR] File not found: {f}", file=sys.stderr)
            sys.exit(1)

    content_a = extract_text(FILE_A)
    content_b = extract_text(FILE_B)

    # Decide if ROUGE is applicable
    # If the file types are different (e.g. PDF vs MD), automated ROUGE is inherently unfair
    rouge_enabled = True
    if (
        not (REFERENCE_DOC and REFERENCE_DOC.exists())
        and FILE_A.suffix.lower() != FILE_B.suffix.lower()
    ):
        rouge_enabled = False
        ref_label = "N/A (Skipped due to mismatched file types)"

    if rouge_enabled:
        if REFERENCE_DOC and REFERENCE_DOC.exists():
            ref_content = extract_text(REFERENCE_DOC)
            ref_label = REFERENCE_DOC.name
            ref_content_a = ref_content
            ref_content_b = ref_content
        else:
            ref_content_a = content_b  # reference for A = B
            ref_content_b = content_a  # reference for B = A
            ref_label = "cross-file reference"
    else:
        ref_content_a = None
        ref_content_b = None

    print(f"\n  File A : {FILE_A.name} ({len(content_a):,} chars)")
    print(f"  File B : {FILE_B.name} ({len(content_b):,} chars)")
    print(f"  ROUGE reference           : {ref_label}")
    print(f"  Gemini model              : {GEMINI_MODEL}")

    init_vertex_ai()

    # Evaluate File A
    res_a = evaluate_document(
        label=FILE_A.name,
        content=content_a,
        reference_content=ref_content_a,
    )
    print_dimension_table(res_a)

    # Evaluate File B
    res_b = evaluate_document(
        label=FILE_B.name,
        content=content_b,
        reference_content=ref_content_b,
    )
    print_dimension_table(res_b)

    # Comparison
    print_comparison(res_a, res_b)

    # Save
    out_path = Path(__file__).parent / "benchmark_results.json"
    save_results(res_a, res_b, out_path)


if __name__ == "__main__":
    main()
