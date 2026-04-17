"""
SalesAgent Package

Lead Generation AI Agent system for company research and analysis.
Contains schemas, prompts, and sub-agents for comprehensive company intelligence.
"""

# Import and re-export schemas
# Import prompts
from .prompts import (
    # Synthesis Prompts
    ALIGNMENT_PROMPT,
    CAMPAIGN_SIGNALS_PROMPT,
    COMPLIANCE_PROMPT,
    ECOSYSTEM_PROMPT,
    EXECUTIVE_PROMPT,
    # Core Business Prompts
    FIRMOGRAPHICS_PROMPT,
    GEOGRAPHIC_PROMPT,
    # Signals Prompts
    GROWTH_SIGNALS_PROMPT,
    MARKET_PROMPT,
    PROCUREMENT_PROMPT,
    REPORT_COMPILER_PROMPT,
    RISK_SIGNALS_PROMPT,
    # Strategy Prompts
    STRATEGY_PROMPT,
    # Tech Prompts
    TECH_STACK_PROMPT,
)
from .schemas import (
    CampaignSignalsModel,
    Certification,
    Challenge,
    # Alignment Models
    ColtAlignmentMapping,
    ColtAlignmentOutput,
    CompanyOverview,
    # Firmographics Models
    CompanySnapshot,
    ComplianceFactors,
    DataCenterInfo,
    DependencyInsight,
    Ecosystem,
    # Leadership Models
    Executive,
    FinancialRelevance,
    # Combined Output
    FullResearchData,
    GlobalOperations,
    GrowthSignalsModel,
    KeyChallenges,
    LeadershipTeam,
    MarketPosition,
    # Geographic Models
    OfficeLocation,
    # Ecosystem Models
    Partner,
    ProcurementPatterns,
    RegionalSpend,
    # Compliance Models
    Regulation,
    # Market Models
    RevenueBreakdown,
    RiskSignalsModel,
    # Signal Models
    Signal,
    SignalsOutput,
    StrategicOpportunitySummary,
    StrategicPriorities,
    # Strategy Models
    StrategicPriority,
    # Technology Models
    TechnologyLandscape,
)

__all__ = [
    # Schemas
    "CompanySnapshot",
    "CompanyOverview",
    "OfficeLocation",
    "DataCenterInfo",
    "RegionalSpend",
    "GlobalOperations",
    "Executive",
    "LeadershipTeam",
    "StrategicPriority",
    "Challenge",
    "StrategicPriorities",
    "KeyChallenges",
    "Regulation",
    "Certification",
    "ComplianceFactors",
    "RevenueBreakdown",
    "MarketPosition",
    "Partner",
    "DependencyInsight",
    "Ecosystem",
    "FinancialRelevance",
    "TechnologyLandscape",
    "ProcurementPatterns",
    "Signal",
    "GrowthSignalsModel",
    "RiskSignalsModel",
    "CampaignSignalsModel",
    "SignalsOutput",
    "ColtAlignmentMapping",
    "StrategicOpportunitySummary",
    "ColtAlignmentOutput",
    "FullResearchData",
    # Prompts
    "GROWTH_SIGNALS_PROMPT",
    "RISK_SIGNALS_PROMPT",
    "CAMPAIGN_SIGNALS_PROMPT",
    "FIRMOGRAPHICS_PROMPT",
    "GEOGRAPHIC_PROMPT",
    "EXECUTIVE_PROMPT",
    "STRATEGY_PROMPT",
    "COMPLIANCE_PROMPT",
    "MARKET_PROMPT",
    "ECOSYSTEM_PROMPT",
    "TECH_STACK_PROMPT",
    "PROCUREMENT_PROMPT",
    "ALIGNMENT_PROMPT",
    "REPORT_COMPILER_PROMPT",
]
