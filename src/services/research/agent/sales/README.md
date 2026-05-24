# Sales Research Agent Architecture

The following diagram illustrates the proposed architecture for the Sales Research Agent.

```mermaid
graph TD
    User([User]) -->|Company Name| MainAgent["SalesResearchAgent <br/> <i>SequentialAgent</i>"]
    
    subgraph ParallelProcessing ["Phase 1: Massive Parallel Research"]
        direction TB
        MainAgent --> ParGroup["ResearchOrchestrator <br/> <i>ParallelAgent</i>"]
        
        %% Core Business
        ParGroup --> A1["FirmographicsAgent <br/> <i>Snapshot, Overview (1)</i>"]
        ParGroup --> A2["GeographicAgent <br/> <i>Locations (1.1, 10)</i>"]
        ParGroup --> A3["ExecutivePipeline <br/> <i>Leadership & Bios (2)</i>"]
        
        %% Strategy & Market
        ParGroup --> A4["StrategyAgent <br/> <i>Goals (3), Challenges (6)</i>"]
        ParGroup --> A5["ComplianceAgent <br/> <i>Regs (5.1), Certs</i>"]
        ParGroup --> A6["MarketAgent <br/> <i>Market (4), Finance (6.1)</i>"]
        ParGroup --> A7["EcosystemAgent <br/> <i>Partners (4.1), Relations (9)</i>"]
        
        %% Tech & Ops
        ParGroup --> A8["TechStackAgent <br/> <i>Landscape (5)</i>"]
        ParGroup --> A9["ProcurementAgent <br/> <i>Buying (7)</i>"]
        
        %% Signal Specialists (Grouped as requested)
        ParGroup --> SigOrch["SignalsOrchestrator <br/> <i>ParallelAgent</i>"]
        SigOrch --> S1["GrowthSignals <br/> <i>Hiring, M&A</i>"]
        SigOrch --> S2["RiskSignals <br/> <i>Security, Regs</i>"]
        SigOrch --> S3["CampaignSignals <br/> <i>Ads, Events</i>"]
        
        %% Outputs
        A1 & A2 & A3 & A4 & A5 & A6 & A7 & A8 & A9 & S1 & S2 & S3 --> Context[Shared Context]
    end

    subgraph SynthesisProcessing ["Phase 2: Synthesis & Reporting"]
        MainAgent --> AlignmentAgent["AlignmentAnalyst <br/> <i>LlmAgent</i>"]
        
        Context --> AlignmentAgent
        AlignmentAgent -->|Colt Alignment Strategy| Context
        
        MainAgent --> Compiler["ReportCompiler <br/> <i>LlmAgent</i>"]
        Context --> Compiler
        Compiler -->|Final Markdown Report| User
    end

    style MainAgent fill:#f9f,stroke:#333,stroke-width:2px
    style ParGroup fill:#ccf,stroke:#333,stroke-width:2px
    style Context fill:#ffe,stroke:#333,stroke-width:2px
```

## Agent Roles (Scraping Categories)

1.  **SalesResearchAgent**: The main host.
2.  **Specialized Scopes**: Each agent maps exactly to 1-2 derived sections of your template.
3.  **Logical Grouping**:
    *   **FirmographicsAgent**: Snapshot, Overview (1).
    *   **GeographicAgent**: Global Ops (1.1), Regional Spend (10).
    *   **ExecutivePipeline**: Leaders + Bios (2).
    *   **StrategyAgent**: Priorities (3), Challenges (6).
    *   **ComplianceAgent**: Regulations (5.1).
    *   **MarketAgent**: Market Position (4), Financial Relevance (6.1).
    *   **EcosystemAgent**: Partners (4.1), Relationships (9).
    *   **TechStackAgent**: Tech Landscape (5).
    *   **ProcurementAgent**: Procurement (7).
    *   **SignalsOrchestrator**: Parent agent managing the Signals category (13).
        *   `GrowthSignals`: Hiring, M&A.
        *   `RiskSignals`: Security, Regulations.
        *   `CampaignSignals`: Campaigns, Ads.
4.  **AlignmentAnalyst**: Generates "8. Colt Technology Alignment" and "11. Strategic Opportunity Summary".
5.  **ReportCompiler**: Assembles Sections 1-13 + Source Summary into the final report.

## Optimization Analysis

**The "Flat Parallel" Architecture**:
1.  **Massive Parallelism**: All 12 specialized agents start immediately. We don't wait for a "Strategy Manager" to call a "Compliance Worker".
2.  **Tool-Level Caching**: We rely on shared tools to prevent duplicate work. If `StrategyAgent` and `FirmographicsAgent` both request the same URL, the second request is served from the cache, enabling efficiency without sacrificing the quality benefits of specialized agents.
3.  **Cost Efficiency**: We removed intermediate "management" agents, paying only for the actual research work.
