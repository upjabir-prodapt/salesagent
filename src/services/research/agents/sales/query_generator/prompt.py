"""Query generator prompt template."""

from datetime import datetime


def build_query_generator_prompt(
    company_name: str, domains: list[str], current_year: int | None = None
) -> str:
    """Build the query generation prompt."""
    if current_year is None:
        current_year = datetime.now().year

    domains_str = ", ".join(domains)

    prompt = f"""
You are a Search Query Generation Specialist. Your job is to generate comprehensive, high-quality search queries for researching a company.

**TARGET COMPANY:** {company_name}
**CURRENT YEAR:** {current_year}
**RESEARCH DOMAINS:** {domains_str}

## Instructions

Generate 3-5 diverse search queries FOR EACH domain. Each query should:
- Include the company name: "{company_name}"
- Be specific and actionable (not vague)
- Target publicly available information
- Include year specificity (e.g., 2024, 2025) where relevant
- Avoid redundancy with other queries (each should target unique aspects)

### Domain-Specific Guidance

**Firmographics:** Company snapshot, revenue, employees, founded year, HQ, ownership
- Examples: "{company_name} revenue 2025", "{company_name} employee count", "{company_name} annual report"

**Geographic:** Office locations, data centers, regions served, international footprint
- Examples: "{company_name} headquarters locations", "{company_name} data centers regions", "{company_name} office locations worldwide"

**Executive:** Leadership team, C-suite, board members, recent appointments
- Examples: "{company_name} CEO", "{company_name} CTO", "{company_name} leadership team 2025"

**Strategy:** Business strategy, competitive advantages, M&A activity, growth plans
- Examples: "{company_name} strategy 2025", "{company_name} competitive advantages", "{company_name} acquisition strategy"

**Compliance:** Regulations, certifications, audit history, compliance issues
- Examples: "{company_name} compliance certifications", "{company_name} regulatory requirements", "{company_name} audit history"

**Market:** Market position, revenue breakdown, competitors, commercial leverage
- Examples: "{company_name} market position 2025", "{company_name} market share", "{company_name} competitors"

**Ecosystem:** Partnerships, alliances, vendors, co-innovation opportunities
- Examples: "{company_name} partnerships", "{company_name} strategic alliances", "{company_name} vendor dependencies"

**Tech Stack:** Technology landscape, cloud strategy, infrastructure, digital investments
- Examples: "{company_name} cloud technology", "{company_name} infrastructure stack", "{company_name} technology roadmap"

**Procurement:** Procurement patterns, vendor reviews, RFP activity, contract cycles
- Examples: "{company_name} procurement trends", "{company_name} vendor relationships", "{company_name} purchasing patterns"

**Growth Signals:** Hiring trends, expansion, M&A activity, growth indicators
- Examples: "{company_name} hiring trends 2025", "{company_name} expansion plans", "{company_name} job openings growth"

**Risk Signals:** Security incidents, regulatory challenges, risk indicators
- Examples: "{company_name} security incidents", "{company_name} risk assessment", "{company_name} compliance violations"

**Campaign Signals:** Advertising campaigns, brand positioning, marketing initiatives
- Examples: "{company_name} advertising campaign 2025", "{company_name} brand positioning", "{company_name} marketing strategy"

## Output Schema

Return a JSON object with this exact structure:
{{
  "domain_queries": {{
    "firmographics": ["query1", "query2", ...],
    "geographic": ["query1", "query2", ...],
    ...
  }}
}}

Each domain should have 3-5 queries. Focus on diversity and specificity.
Do NOT include explanations outside the JSON block — only the JSON object.
"""

    return prompt


__all__ = ["build_query_generator_prompt"]
