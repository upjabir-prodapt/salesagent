from dataclasses import dataclass, field

from .url_utils import is_authoritative


@dataclass
class GroundingReport:
    source_urls: list[str] = field(default_factory=list)
    low_confidence_claims: list[dict] = field(default_factory=list)
    non_authoritative_urls: list[str] = field(default_factory=list)


def extract_grounding_report(llm_response) -> GroundingReport:
    report = GroundingReport()
    try:
        # Support both ADK LlmResponse and raw types.GenerateContentResponse
        candidates = getattr(llm_response, "candidates", [])
        if not candidates:
            return report

        metadata = getattr(candidates[0], "grounding_metadata", None)
        if not metadata:
            return report
    except (AttributeError, IndexError):
        return report

    for chunk in getattr(metadata, "grounding_chunks", None) or []:
        uri = getattr(getattr(chunk, "web", None), "uri", None)
        if uri:
            if uri not in report.source_urls:
                report.source_urls.append(uri)
            if not is_authoritative(uri) and uri not in report.non_authoritative_urls:
                report.non_authoritative_urls.append(uri)

    for support in getattr(metadata, "grounding_supports", None) or []:
        scores = getattr(support, "confidence_scores", None) or []
        if any(s < 0.7 for s in scores):
            report.low_confidence_claims.append(
                {
                    "text": getattr(getattr(support, "segment", None), "text", ""),
                    "min_score": min(scores) if scores else 0.0,
                }
            )

    return report
