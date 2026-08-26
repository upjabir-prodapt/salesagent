"""Configuration constants for evaluation scoring."""

from __future__ import annotations

from typing import Any

from src.worker.domain.contracts import AGENT_OUTPUT_KEYS

DIMENSION_CONFIG: dict[str, dict[str, Any]] = {
    "D1_data_currency_recency": {
        "weight": 2.0,
        "category": "Factual Accuracy",
        "description": "Data Currency & Recency — Stale data is actively harmful to sales conversations",
    },
    "D2_executive_intelligence_bios": {
        "weight": 2.0,
        "category": "Stakeholder Mapping",
        "description": "Executive Intelligence & Bios — Accurate decision-maker mapping is critical",
    },
    "D3_cybersecurity_context": {
        "weight": 2.0,
        "category": "Pain Point ID",
        "description": "Cybersecurity Context — Direct alignment with Colt's core offering domain",
    },
    "D4_strategic_priorities_alignment": {
        "weight": 1.5,
        "category": "Business Drivers",
        "description": "Strategic Priorities Alignment — Foundation for 'Why Now?' narrative",
    },
    "D5_technology_landscape_detail": {
        "weight": 1.5,
        "category": "Tech Stack Mapping",
        "description": "Technology Landscape Detail — Identifies displacement opportunities for Colt",
    },
    "D6_colt_solution_alignment_table": {
        "weight": 2.0,
        "category": "Pitch Readiness",
        "description": "Colt Solution Alignment Table — Directly measures RAG effectiveness and output utility",
    },
    "D7_procurement_buying_signals": {
        "weight": 1.5,
        "category": "Sales Timing",
        "description": "Procurement & Buying Signals — Enables sellers to time outreach effectively",
    },
    "D8_financial_trading_relevance": {
        "weight": 1.5,
        "category": "Commercial Context",
        "description": "Financial & Trading Relevance — Supports business case construction",
    },
    "D9_global_operations_footprint": {
        "weight": 1.0,
        "category": "Geographic Targeting",
        "description": "Global Operations & Footprint — Useful but secondary to core intelligence",
    },
    "D10_regulatory_compliance_detail": {
        "weight": 1.0,
        "category": "Risk Angle",
        "description": "Regulatory & Compliance Detail — Context for compliance-driven sales motions",
    },
    "D11_relationship_ecosystem_mapping": {
        "weight": 1.0,
        "category": "Partner Landscape",
        "description": "Relationship & Ecosystem Mapping — Useful for partnership and channel strategy",
    },
    "D12_sustainability_esg_alignment": {
        "weight": 1.0,
        "category": "Value Hook",
        "description": "Sustainability / ESG Alignment — Increasingly important, but not primary buying driver",
    },
    "D13_signals_growth_risk_campaign": {
        "weight": 1.0,
        "category": "Real-Time Intel",
        "description": "Signals (Growth, Risk, Campaign) — Provides urgency and timeliness context",
    },
    "D14_why_colt_why_now_summary": {
        "weight": 1.5,
        "category": "Closing Narrative",
        "description": "Why Colt? Why Now? Summary — Directly impacts seller confidence and pitch quality",
    },
}

MAX_SECTION_A_WEIGHTED_SCORE = 82.0

SECTION_B_WEIGHTS = {
    "M1_agent_output_coverage": 0.20,
    "M2_report_completeness": 0.20,
    "M3_citation_groundedness": 0.25,
    "M4_evidence_breadth": 0.15,
    "M5_semantic_groundedness": 0.20,
}

RESEARCH_AGENT_OUTPUT_KEYS = {
    key: value for key, value in AGENT_OUTPUT_KEYS.items() if value != "final_report"
}

MIN_EXPECTED_DOMAINS = 8
EXPECTED_SECTION_COUNT = 13
