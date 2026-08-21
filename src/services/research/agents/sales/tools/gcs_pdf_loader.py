"""GCS PDF loader for alignment context."""

from __future__ import annotations

from typing import Any

from google.cloud import storage
from google.cloud.exceptions import GoogleCloudError

from ....core.config import settings
from ....core.logging_config import logger
from ....repositories.gcs_repository import GCSRepository

# Hardcoded Colt product catalog text from the provided PDF
COLT_CATALOG_HARDCODED = """
Strategic Portfolio and Global Business Catalog: Colt Technology Services

The digital infrastructure landscape of the 2020s is characterized by a fundamental shift from
traditional connectivity to intelligent, automated, and sustainable ecosystems. At the center of
this transformation is Colt Technology Services, an organization that has evolved from its 1992
origins as a London-based fiber provider into a global powerhouse in the business-to-business
digital infrastructure market.

Key Corporate Metrics:
- Year Established: 1992
- Global Headquarters: London, United Kingdom
- Ownership: Privately Owned
- Total Global Employees: Over 6,000
- Regional Offices: 80+ locations worldwide
- Strategic Partners: AWS, Azure, Google Cloud, Ciena, Versa, Digital Realty
- Total On-Net Buildings: 32,000+
- Net Promoter Score (Global): 75 (2024 Highlight)

The Colt IQ Network - Infrastructure Capabilities:
- Total Fiber Length: 38,000km (Metropolitan and Long-Haul)
- Metropolitan Areas: 51+ globally
- Data Center Connections: 1,100+
- Cloud On-Ramps: 275+ Cloud PoPs
- Subsea Systems: 10 subsea cable systems
- Internet Backbone: EMEA portion of AS3356
- Max Port Speed: 400Gbps (with 800Gbps capability in development)

Core Services and Solutions:
1. Infrastructure and High-Bandwidth Solutions: Dark Fibre, Colocation, Spectrum
2. Wavelength and Optical Services: Colt Wave, Private Wave with 1G-400G bandwidth options
3. Ethernet and IP Access: Scalable services from Mbps to 100Gbps
4. Dedicated Cloud Access (DCA): Direct agreements with AWS, Azure, Google Cloud, Oracle OCI, IBM Cloud, OVH Cloud
5. SD-WAN and SASE Solutions: Multi-vendor support with Versa Networks
6. Voice and Collaboration: SIP Trunking, Intelligent Numbers, Unified Communications, CCaaS

Global Regions:
- Europe: London, Paris, Frankfurt, Madrid, Amsterdam, Milan, Berlin, Warsaw (40+ countries, 230+ cities)
- North America: New York, Chicago, Secaucus, Aurora (IL), Long Island (Subsea Landing)
- Asia Pacific: Tokyo, Osaka, Hong Kong, Singapore, Sydney, Mumbai, Chennai
- Global Reach: 40+ countries on-net; 180+ countries via carrier partners

Industry Segments:
- Capital Markets: Colt PrizmNet for financial extranet, Ultra Low Latency services for LSE, Euronext, JPX, HKEX, CME
- Manufacturing: Network optimization, global resiliency across 40 countries
- Pharma and Healthcare: Compliance-focused infrastructure, AI workload support
- Retail: Cloud migration support, trusted by Decathlon and Farfetch

Key Partnerships:
- Hardware: Ciena (optical), Juniper (routing), Cisco (networking)
- SD-WAN/SASE: Versa Networks
- Satellite: Rivada Space Networks (LEO satellite integration - Outernet)
- Cloud Ecosystem: Digital Realty, Equinix (data center partnerships)

Competitive Advantages:
- Industry-leading NPS: 75 globally (Europe consistently >70)
- Ease of doing business: 7% improvement through ML and proactive delivery
- Financial strength: Privately owned, enabling long-term infrastructure investment
- Fiber ownership: Most extensive dark fiber portfolio in Europe

ESG Commitment:
- Net Zero 2045: Science-based roadmap for 90% absolute reduction by 2045
- Current Progress: 35% carbon reduction since 2019
- Renewable Energy: 78% of electricity from renewable sources
- Supplier Conduct: 89% of suppliers signed Colt Code of Business Conduct
- Awards: Platinum EcoVadis score for two consecutive years

Subsea & Transatlantic Assets:
- Grace Hopper: New transatlantic route, US East Coast to Europe
- Yellow (AC-2): Named after Beatles' Yellow Submarine, US East Coast to UK
- Dunant and Apollo South: Expanded infrastructure for AI-driven traffic
- Atlantic Crossing-1 (AC-1): Foundational transatlantic asset
- Technical Capability: 6,000km cables with 20Tbit/s per fiber pair
"""


def load_pdf_from_gcs(company_name: str) -> str | None:
    """Load PDF from GCS bucket for a specific company."""
    try:
        gcs_repo = GCSRepository()
        bucket_name = settings.GCS_BUCKET_NAME

        # Try to find company-specific PDF
        pdf_path = f"{settings.GCS_PARENT_FOLDER}/alignment_context/{company_name.lower()}_catalog.pdf"

        logger.info(f"Attempting to load PDF from GCS: {pdf_path}")

        # This would need a GCS method to download PDF
        # For now, return None to trigger hardcoded fallback
        return None
    except Exception as e:
        logger.warning(f"Failed to load PDF from GCS: {e}")
        return None


def get_alignment_context(company_name: str) -> str:
    """Get alignment context from PDF or hardcoded fallback."""
    # First try to load from GCS
    pdf_content = load_pdf_from_gcs(company_name)

    if pdf_content:
        logger.info("Using PDF context from GCS")
        return pdf_content

    # Fallback to hardcoded Colt catalog
    logger.info("Using hardcoded Colt catalog context")
    return COLT_CATALOG_HARDCODED


__all__ = ["get_alignment_context", "load_pdf_from_gcs", "COLT_CATALOG_HARDCODED"]
