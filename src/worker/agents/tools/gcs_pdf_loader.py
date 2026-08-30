"""Mounted PDF loader for Colt product catalog and alignment context."""

from __future__ import annotations

import threading
import time

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


# Colt's product catalog is byte-identical across every research job for
# every company -- unlike a per-job cache, this amortizes across the
# entire fleet of jobs the worker processes over its lifetime. Verified
# live (2026-08-30): a 24,749-char catalog tokenizes to ~5,372 tokens
# (above the 4,096-token minimum for Gemini 3.x Flash on Vertex AI); a
# generate_content call referencing the resulting cached_content reported
# cached_content_token_count=5360 (near-100% hit), confirming caches.
# create()/cached_content actually work end-to-end in this deployment.
_COLT_CACHE_TTL_SECONDS = 3600
# Refresh a bit before the server-side TTL actually expires, so a request
# arriving right at the boundary doesn't race an expired cache reference.
_COLT_CACHE_REFRESH_MARGIN_SECONDS = 120

_colt_cache_name: str | None = None
_colt_cache_expires_at: float = 0.0
_colt_cache_lock = threading.Lock()


def get_or_create_colt_context_cache(model_name: str | None = None) -> str | None:
    """Create or return the existing Gemini context cache for the Colt
    catalog, refreshing it once the server-side TTL is close to expiring.

    Returns None (never raises) on any failure -- callers must fall back
    to inlining the catalog text directly in the prompt in that case, so
    a cache-service hiccup never blocks report generation.

    NOT CURRENTLY CALLED (2026-08-30): live end-to-end testing showed
    AlignmentAnalyst cannot actually use the resulting cache name. Gemini
    rejects a request that sets `cached_content` together with
    `system_instruction`/`tools` ("Tool config, tools and system
    instruction should not be set in the request when using cached
    content."), and ADK's LlmAgent unconditionally injects a
    system_instruction identity block for every root agent
    (google.adk.flows.llm_flows.identity: `if agent.mode != 'single_turn'`)
    with no way to suppress it -- `mode='single_turn'` is itself rejected
    for a root agent under Runner ("LlmAgent as root agent must have
    mode='chat'"). This function (and the cache-creation logic below) is
    kept, working and tested, in case a future ADK version exposes a way
    to build a request without the identity injection.
    """
    global _colt_cache_name, _colt_cache_expires_at

    with _colt_cache_lock:
        if _colt_cache_name and time.monotonic() < _colt_cache_expires_at:
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
                    "ttl": f"{_COLT_CACHE_TTL_SECONDS}s",
                    "display_name": "colt_product_catalog_cache",
                },
            )
            _colt_cache_name = cache.name
            _colt_cache_expires_at = time.monotonic() + (
                _COLT_CACHE_TTL_SECONDS - _COLT_CACHE_REFRESH_MARGIN_SECONDS
            )
            logger.info(
                f"[ContextCache] Created Colt catalog context cache: "
                f"{_colt_cache_name} (expires in {_COLT_CACHE_TTL_SECONDS}s)"
            )
            return _colt_cache_name
        except Exception as exc:
            logger.warning(
                f"[ContextCache] Context cache creation failed; "
                f"AlignmentAnalyst will fall back to inlining the catalog "
                f"text directly in the prompt: {exc}"
            )
            _colt_cache_name = None
            _colt_cache_expires_at = 0.0
            return None


__all__ = [
    "get_alignment_context",
    "load_mounted_colt_catalog_pdf",
    "COLT_CATALOG_HARDCODED",
    "get_or_create_colt_context_cache",
]
