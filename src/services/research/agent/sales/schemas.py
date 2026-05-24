from pydantic import BaseModel, Field


# --- Section 1: Firmographics ---
class CompanySnapshot(BaseModel):
    company_name: str = Field(..., description="Official company name")
    sector: str = Field(..., description="Industry sector")
    sub_industry: str | None = Field(None, description="Sub-industry classification")
    global_revenue: str = Field(..., description="Most recent annual revenue")
    previous_revenue: str = Field(
        ..., description="Previous year revenue for growth calc"
    )
    employee_count: str = Field(..., description="Total employee count")
    estimated_it_spend: str = Field(..., description="Estimated IT budget/spend")
    market_cap: str | None = Field(None, description="Market capitalization if public")
    public_private_status: str | None = Field(
        None, description="Public or Private status"
    )
    stock_ticker: str | None = Field(None, description="Stock ticker symbol if public")
    founded_year: int | None = Field(None, description="Year company was founded")
    ownership_structure: str | None = Field(
        None, description="Standalone, subsidiary, parent company, etc."
    )
    website: str | None = Field(None, description="Official website URL")
    summary: str = Field(..., description="2-4 sentence summary of core business")


class CompanyOverview(BaseModel):
    legal_name: str = Field(..., description="Full legal entity name")
    hq_location: str = Field(..., description="City and Country of HQ")
    business_model: str = Field(
        ..., description="Paragraph on business model and revenue stream"
    )


# --- Section 1.1: Geographic & Regional ---
class OfficeLocation(BaseModel):
    city: str = Field(..., description="City name")
    country: str = Field(..., description="Country name")
    region: str | None = Field(None, description="Geographic region")
    office_type: str | None = Field(
        None, description="HQ, Regional, R&D, Sales, Data Center, etc."
    )


class DataCenterInfo(BaseModel):
    location: str = Field(..., description="Data center location")
    region: str = Field(..., description="Geographic region")
    providers: list[str] = Field(
        default_factory=list, description="Cloud/infra providers"
    )
    primary_use: str | None = Field(None, description="Primary use case")


class RegionalSpend(BaseModel):
    region: str = Field(..., description="Geographic region")
    estimated_spend: str | None = Field(
        None, description="Estimated IT spend in region"
    )
    percentage_of_total: str | None = Field(
        None, description="Percentage of total spend"
    )


class GlobalOperations(BaseModel):
    hq_country: str = Field(..., description="Country of HQ")
    office_locations: list[OfficeLocation] = Field(
        default_factory=list, description="Detailed office locations"
    )
    regional_offices: list[str] = Field(
        ..., description="Key regional offices (legacy)"
    )
    key_sites: list[str] = Field(..., description="Manufacturing, R&D, or Data centers")
    data_centers: list[DataCenterInfo] = Field(
        default_factory=list, description="Data center details"
    )
    trading_regions: list[str] = Field(..., description="Top 3 trading regions")
    regional_revenue_distribution: list[RegionalSpend] = Field(
        default_factory=list, description="Revenue by region"
    )
    countries_of_operation: list[str] = Field(
        default_factory=list, description="All countries of operation"
    )
    expansion_plans: list[str] = Field(
        default_factory=list, description="Geographic expansion plans"
    )
    supply_chain: str | None = Field(None, description="Supply chain dependencies")


# --- Section 2: Leadership ---
class Executive(BaseModel):
    name: str = Field(..., description="Full name")
    role: str = Field(..., description="Current Job Title")
    department: str | None = Field(None, description="Department or function")
    start_date: str = Field(..., description="Start date in role")
    tenure_years: float | None = Field(None, description="Years in current role")
    previous_roles: str = Field(..., description="Top 2-3 previous roles")
    education: str = Field(..., description="Education history")
    linkedin_url: str | None = Field(None, description="LinkedIn profile URL")
    email: str | None = Field(None, description="Email address if available")
    phone: str | None = Field(None, description="Phone number if available")
    notable_achievements: str | None = Field(
        None, description="Notable career achievements"
    )
    quote: str = Field(..., description="Public statement or strategic quote")
    leadership_style: str | None = Field(
        None,
        description="Observed leadership persona (e.g., Change Agent, Cost Cutter)",
    )
    call_hooks: list[str] = Field(
        default_factory=list,
        description="3-5 ready-to-use conversation starters based on background/quotes",
    )


class LeadershipTeam(BaseModel):
    ceo: Executive | None = None
    cio: Executive | None = None
    cto: Executive | None = None
    ciso: Executive | None = None
    cfo: Executive | None = None
    coo: Executive | None = None
    other_key_leaders: list[Executive] = Field(default_factory=list)
    board_members: list[Executive] = Field(
        default_factory=list, description="Board of directors"
    )
    key_influencers: list[Executive] = Field(
        default_factory=list, description="Key decision-makers and influencers"
    )
    recent_leadership_changes: list[str] = Field(
        default_factory=list, description="Recent leadership changes or appointments"
    )


# --- Section 3: Strategy ---
class StrategicPriority(BaseModel):
    priority: str = Field(..., description="Strategic priority name")
    description: str | None = Field(None, description="Detailed description")
    timeline: str | None = Field(None, description="Expected timeline")


class Challenge(BaseModel):
    challenge_type: str = Field(
        ...,
        description="Type: operational, financial, competitive, regulatory, technical",
    )
    description: str = Field(..., description="Challenge description")
    impact: str | None = Field(None, description="Business impact")
    commercial_impact: str | None = Field(
        None, description="Commercial impact: cost, revenue, margin, risk exposure"
    )
    quantified_impact: str | None = Field(
        None, description="Specific currency/percent metrics related to the challenge"
    )
    colt_so_what: str | None = Field(
        None, description="Direct relevance to Colt's network/security capabilities"
    )


class StrategicPriorities(BaseModel):
    strategic_priorities: list[StrategicPriority] = Field(
        default_factory=list, description="Structured strategic priorities"
    )
    transformation_goals: list[str] = Field(..., description="Growth, efficiency, etc.")
    digital_plans: list[str] = Field(..., description="Cloud, AI, Automation plans")
    cloud_migration_strategy: str | None = Field(
        None, description="Cloud migration approach"
    )
    investment_areas: list[str] = Field(
        default_factory=list, description="Key investment focus areas"
    )
    m_and_a_strategy: str | None = Field(
        None, description="Mergers and acquisitions strategy"
    )
    market_expansion_plans: list[str] = Field(
        default_factory=list, description="Geographic/market expansion"
    )
    competitive_advantages: list[str] = Field(
        default_factory=list, description="Key competitive differentiators"
    )
    sustainability_targets: list[str] = Field(..., description="Net-zero, ESG targets")
    quotes: list[str] = Field(..., description="Relevant leadership quotes")


class Regulation(BaseModel):
    regulation_name: str = Field(..., description="Name of regulation")
    region: str | None = Field(None, description="Applicable region")
    compliance_status: str | None = Field(
        None, description="Compliant, Non-compliant, In Progress"
    )
    details: str | None = Field(None, description="Additional details")


class Certification(BaseModel):
    name: str = Field(..., description="Certification name")
    issuer: str | None = Field(None, description="Issuing body")
    expiration_date: str | None = Field(
        None, description="Expiration date if applicable"
    )


class ComplianceFactors(BaseModel):
    applicable_regulations: list[Regulation] = Field(
        default_factory=list, description="Detailed regulations"
    )
    regulatory_bodies: list[str] = Field(..., description="Core regulators (legacy)")
    data_sovereignty: str = Field(..., description="Data residency requirements")
    industry_certifications: list[Certification] = Field(
        default_factory=list, description="Detailed certifications"
    )
    certifications: list[str] = Field(..., description="ISO, NIST, etc. (legacy)")
    audit_history: list[str] = Field(
        default_factory=list, description="Past audit findings"
    )
    data_privacy_policies: str | None = Field(
        None, description="Data privacy and handling practices"
    )
    security_frameworks: list[str] = Field(
        default_factory=list, description="Security frameworks in use"
    )
    known_compliance_issues: list[str] = Field(
        default_factory=list, description="Known issues or violations"
    )
    challenges: str = Field(..., description="Known compliance issues or fines")


class KeyChallenges(BaseModel):
    challenges: list[Challenge] = Field(
        default_factory=list, description="Structured challenges"
    )
    operational: str = Field(..., description="Operational complexity challenges")
    cost_pressures: str = Field(..., description="Cost reduction drivers")
    cybersecurity: str = Field(..., description="Threats and risks")
    performance: str = Field(..., description="Latency/Scalability issues")
    external: str = Field(..., description="Geopolitical/Regulatory pressure")


# --- Section 4: Market ---
class RevenueBreakdown(BaseModel):
    geography: str | None = Field(None, description="Geographic region")
    segment: str | None = Field(None, description="Business segment")
    product_line: str | None = Field(None, description="Product or service line")
    amount: str | None = Field(None, description="Revenue amount")


class MarketPosition(BaseModel):
    revenue_breakdown_detailed: list[RevenueBreakdown] = Field(
        default_factory=list, description="Detailed revenue breakdown"
    )
    revenue_breakdown: str = Field(..., description="Revenue by geo/segment (legacy)")
    competitive_landscape: str = Field(..., description="Market share and competitors")
    market_share: str | None = Field(None, description="Estimated market share")
    market_challenges: str = Field(..., description="Key market headwinds")
    global_trends: list[str] = Field(
        default_factory=list, description="Relevant global market trends"
    )
    emerging_areas: str = Field(..., description="New product/service focus")
    key_customers: list[str] = Field(
        default_factory=list, description="Major customers"
    )
    procurement_model: str | None = Field(
        None, description="Centralized, regional, or hybrid"
    )
    commercial_leverage_points: list[str] = Field(
        default_factory=list, description="Points of commercial leverage for Colt"
    )


# --- Section 4.1: Ecosystem ---
class Partner(BaseModel):
    name: str = Field(..., description="Partner organization name")
    type: str = Field(..., description="cloud, integrator, OEM, carrier, etc.")
    relationship_type: str | None = Field(
        None, description="Strategic customer, alliance, vendor, etc."
    )


class DependencyInsight(BaseModel):
    competitor_or_provider: str = Field(
        ..., description="Competitor or existing provider"
    )
    action: str = Field(..., description="complement, displace, or expand")
    context: str = Field(..., description="Context and rationale")


class Ecosystem(BaseModel):
    key_partners: list[Partner] = Field(
        default_factory=list, description="Detailed partner information"
    )
    tech_partners: list[str] = Field(
        ..., description="Cloud, Integrators, OEMs (legacy)"
    )
    connectivity_partners: list[str] = Field(..., description="Carriers/Telcos")
    strategic_alliances: list[str] = Field(..., description="Key business alliances")
    dependencies_relative_to_colt: list[DependencyInsight] = Field(
        default_factory=list, description="Colt positioning insights"
    )
    colt_opportunities: str = Field(..., description="Where Colt complements/displaces")
    shared_industry_bodies: list[str] = Field(
        default_factory=list, description="Shared industry memberships"
    )
    historic_colt_engagement: str | None = Field(
        None, description="Past engagement with Colt"
    )
    esg_dei_alignment: str | None = Field(
        None, description="ESG and DEI alignment with Colt"
    )
    co_innovation_potential: list[str] = Field(
        default_factory=list,
        description="Cloud, edge, 5G, AI co-innovation opportunities",
    )
    strategic_fit_summary: str | None = Field(
        None, description="Overall strategic fit assessment"
    )
    relationship_synergies: str = Field(
        ..., description="Shared customers/values (Section 9)"
    )


class FinancialRelevance(BaseModel):
    yoy_growth: str = Field(..., description="Year over year growth percentage")
    cost_drivers: str = Field(..., description="Major costs (energy, logistics)")
    key_cost_drivers: list[str] = Field(
        default_factory=list, description="Detailed cost drivers"
    )
    capex: str = Field(..., description="Capital expenditure plans")
    major_capex: list[str] = Field(
        default_factory=list, description="Major capital expenditures"
    )
    supply_chain_exposure: str = Field(..., description="Supply chain risks")


# --- Section 5: Technographics ---
class TechnologyLandscape(BaseModel):
    cloud_strategy: str = Field(..., description="Cloud/Network approach")
    it_cloud_approach: str | None = Field(
        None, description="Detailed IT and cloud strategy"
    )
    network_cybersecurity_approach: str | None = Field(
        None, description="Network and security approach"
    )
    vendors: list[str] = Field(..., description="Known vendors/platforms")
    infrastructure_models: list[str] = Field(
        default_factory=list, description="On-prem, hybrid, multi-cloud, etc."
    )
    digital_investments: str = Field(..., description="AI/Automation investments")
    ai_automation_investments: list[str] = Field(
        default_factory=list, description="Detailed AI/automation initiatives"
    )
    digital_partnerships_initiatives: list[str] = Field(
        default_factory=list, description="Digital partnership programs"
    )
    innovation_initiatives: str = Field(..., description="Recent digital projects")


class ProcurementPatterns(BaseModel):
    structure: str = Field(..., description="Centralized vs Regional")
    contract_cycles: str = Field(..., description="Typical lengths/renewals")
    renewal_cycles: str | None = Field(None, description="Renewal timing patterns")
    preferred_partners: str = Field(..., description="Existing frameworks")
    preferred_partners_agreements: list[str] = Field(
        default_factory=list, description="Detailed partner agreements"
    )
    budget_trends: str = Field(..., description="Spending signals")
    it_budget_trends: str | None = Field(
        None, description="Detailed IT budget analysis"
    )
    spend_signals: list[str] = Field(
        default_factory=list, description="Investment indicators"
    )
    rfp_activity: list[str] = Field(
        default_factory=list, description="Recent RFP activity"
    )
    vendor_reviews: list[str] = Field(
        default_factory=list, description="Vendor evaluation activities"
    )


# --- Section 13: Signals ---
class Signal(BaseModel):
    signal_type: str = Field(..., description="e.g., Hiring, M&A, Cloud Migration")
    description: str = Field(..., description="Details of the signal")
    source: str = Field(..., description="URL or Source of the finding")
    relevance: str = Field(..., description="Why this matters for sales")


class GrowthSignalsModel(BaseModel):
    hiring_trends: list[str] = Field(
        default_factory=list, description="Hiring patterns and roles"
    )
    ma_activity: list[str] = Field(
        default_factory=list, description="Mergers and acquisitions activity"
    )
    expansion_plans: list[str] = Field(
        default_factory=list, description="Geographic/business expansion"
    )
    signals: list[Signal] = Field(
        default_factory=list, description="Detailed growth signals"
    )


class RiskSignalsModel(BaseModel):
    security_incidents: list[str] = Field(
        default_factory=list, description="Security breaches or incidents"
    )
    regulatory_challenges: list[str] = Field(
        default_factory=list, description="Regulatory issues"
    )
    compliance_issues: list[str] = Field(
        default_factory=list, description="Compliance violations or concerns"
    )
    signals: list[Signal] = Field(
        default_factory=list, description="Detailed risk signals"
    )


class CampaignSignalsModel(BaseModel):
    active_campaigns: list[str] = Field(
        default_factory=list, description="Current marketing campaigns"
    )
    advertising_spend_trends: str | None = Field(default=None, description="Ad spend patterns")
    brand_positioning: str | None = Field(
        default=None, description="Brand strategy and positioning"
    )
    signals: list[Signal] = Field(
        default_factory=list, description="Detailed campaign signals"
    )


class SignalsOutput(BaseModel):
    growth: GrowthSignalsModel = Field(
        default_factory=lambda: GrowthSignalsModel(), description="Growth-related signals"
    )
    risk: RiskSignalsModel = Field(
        default_factory=lambda: RiskSignalsModel(), description="Risk-related signals"
    )
    campaign: CampaignSignalsModel = Field(
        default_factory=lambda: CampaignSignalsModel(), description="Campaign-related signals"
    )
    growth_signals: list[Signal] = Field(default_factory=list, description="Legacy")
    risk_signals: list[Signal] = Field(default_factory=list, description="Legacy")
    campaign_signals: list[Signal] = Field(default_factory=list, description="Legacy")


# --- Combined Research Output ---
class FullResearchData(BaseModel):
    snapshot: CompanySnapshot | None = None
    overview: CompanyOverview | None = None
    global_ops: GlobalOperations | None = None
    leadership: LeadershipTeam | None = None
    strategy: StrategicPriorities | None = None
    compliance: ComplianceFactors | None = None
    challenges: KeyChallenges | None = None
    market: MarketPosition | None = None
    ecosystem: Ecosystem | None = None
    financials: FinancialRelevance | None = None
    technology: TechnologyLandscape | None = None
    procurement: ProcurementPatterns | None = None
    signals: SignalsOutput | None = None


# --- Section 8: Colt Alignment Output ---
class ColtAlignmentMapping(BaseModel):
    challenge_or_priority: str = Field(
        ..., description="Business or IT challenge/priority from the target company"
    )
    colt_solution: str = Field(
        ..., description="Colt solution enabler(s) that address this challenge"
    )
    alignment_justification: str = Field(
        ...,
        description="Punchy, commercial pitch explaining exactly why Colt is relevant, and the absolute value delivered",
    )


class UseCaseRecommendation(BaseModel):
    use_case: str = Field(
        description="The type of meeting (e.g., Executive Discovery Call, Deep Capital Markets Discussion, CIO/CISO Strategic Meeting)"
    )
    recommended_narrative: str = Field(
        description="The best approach or narrative to use for this specific type of meeting."
    )


class StrategicOpportunitySummary(BaseModel):
    summary: str = Field(
        description="A concise single-paragraph summary answering 'Why Colt? Why Now?'"
    )
    hooks: list[str] = Field(
        description="Compelling opening statements based on their challenges."
    )
    executive_narratives: list[str] = Field(
        description="The overarching storyline tying Colt to their C-Suite priorities."
    )
    regulatory_triggers: list[str] = Field(
        description="Recent fines or mandates creating urgency for Colt's secure network."
    )
    ai_urgency: list[str] = Field(
        description="How their AI rollout hinges on Colt's low-latency/high-bandwidth infrastructure."
    )
    competitive_displacement_angles: list[str] = Field(
        description="Where Colt can unseat legacy carriers or unmanaged internet."
    )
    colt_differentiation: list[str] = Field(
        description="Specific Colt products and SLA guarantees that win the deal."
    )
    use_case_recommendations: list[UseCaseRecommendation] = Field(
        description="Recommendations on how to approach different types of sales meetings."
    )


class ColtAlignmentOutput(BaseModel):
    alignment_mappings: list[ColtAlignmentMapping] = Field(
        ..., description="5-7 tailored mappings of challenges to Colt solutions"
    )
    strategic_opportunity: StrategicOpportunitySummary = Field(
        ..., description="Section 11 Strategic Opportunity Summary"
    )


class FinalReport(BaseModel):
    company_name: str = Field(..., description="Name of the company")
    company_snapshot: dict = Field(..., description="Key stats and summary")
    company_overview: dict = Field(..., description="Legal name, HQ, leadership")
    global_operations: dict = Field(..., description="Geographic footprint and sites")
    executive_bios: list[dict] = Field(..., description="Bios of key executives")
    strategic_priorities: dict = Field(
        ..., description="Business goals and digital plans"
    )
    market_position: dict = Field(..., description="Market analysis and landscape")
    technology_landscape: dict = Field(..., description="IT, cloud, and AI approach")
    compliance_factors: dict = Field(
        ..., description="Regulatory and security landscape"
    )
    business_challenges: dict = Field(
        ..., description="Key challenges and financial relevance"
    )
    procurement_patterns: dict = Field(..., description="Buying structure and cycles")
    colt_alignment: list[dict] = Field(..., description="Mapping to Colt solutions")
    strategic_opportunity: dict = Field(..., description="Why Colt? Why Now?")
    signals: dict = Field(..., description="Growth, risk, and campaign signals")
    sources: list[str] = Field(..., description="All source URLs and citations")
