"""Mounted PDF loader for Colt product catalog and alignment context."""

from __future__ import annotations

import pypdf

from src.shared.config import settings
from src.shared.logging_config import logger

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

_extracted_catalog_text: str | None = None


def load_mounted_colt_catalog_pdf() -> str | None:
    """Extract and cache text from the mounted ColtProductCatalog.pdf file."""
    global _extracted_catalog_text
    if _extracted_catalog_text is not None:
        return _extracted_catalog_text

    catalog_path = settings.colt_catalog_path
    if not catalog_path.is_file():
        if not settings.IS_LOCAL:
            raise FileNotFoundError(
                f"Required ColtProductCatalog.pdf not found at mounted path: {catalog_path}. "
                "Ensure the asset is mounted into Cloud Run."
            )
        logger.warning(
            f"[CatalogLoader] Mounted PDF not found at {catalog_path}; falling back to embedded catalog"
        )
        return None

    try:
        reader = pypdf.PdfReader(str(catalog_path))
        extracted = "\n\n".join(
            page.extract_text() or "" for page in reader.pages
        ).strip()
        if extracted:
            _extracted_catalog_text = extracted
            logger.info(
                f"[CatalogLoader] Successfully loaded mounted catalog from {catalog_path} "
                f"({len(reader.pages)} pages, {len(extracted)} chars)"
            )
            return _extracted_catalog_text
    except Exception as e:
        logger.error(f"[CatalogLoader] Error extracting text from {catalog_path}: {e}")
        if not settings.IS_LOCAL:
            raise

    return None


def get_alignment_context(company_name: str) -> str:
    """Get alignment context from mounted ColtProductCatalog.pdf or fallback."""
    pdf_content = load_mounted_colt_catalog_pdf()
    if pdf_content:
        return pdf_content

    logger.info("Using hardcoded Colt catalog context fallback")
    return COLT_CATALOG_HARDCODED


_colt_cache_name: str | None = None


def get_or_create_colt_context_cache(model_name: str | None = None) -> str | None:
    """Create or return existing Gemini context cache for the Colt catalog."""
    global _colt_cache_name
    if _colt_cache_name:
        return _colt_cache_name

    try:
        from src.shared.repositories.clients import get_genai_client

        client = get_genai_client()
        target_model = model_name or settings.GEMINI_MODEL
        catalog_text = get_alignment_context("default")

        cache = client.caches.create(
            model=target_model,
            config={
                "contents": [catalog_text],
                "ttl": "3600s",
                "display_name": "colt_product_catalog_cache",
            },
        )
        _colt_cache_name = cache.name
        logger.info(
            f"[ContextCache] Created Colt catalog context cache: {_colt_cache_name}"
        )
        return _colt_cache_name
    except Exception as exc:
        logger.debug(f"[ContextCache] Context cache creation skipped/fallback: {exc}")
        return None


__all__ = [
    "get_alignment_context",
    "load_mounted_colt_catalog_pdf",
    "COLT_CATALOG_HARDCODED",
    "get_or_create_colt_context_cache",
]
