"""Script to run local research for Sephora programmatically without upping the FastAPI server."""

import asyncio
import sys
import os

# Add the Sales-Agent directory to path so imports work correctly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.core.logging_config import setup_logging, logger
from src.dependencies.service_dependencies import get_research_service

# Sections the ReportCompiler fills with this exact string when its injected
# state keys are empty. A report where most sections say this is the failure
# mode this script exists to catch.
NO_DATA_MARKER = "Data not available from research."
MAX_NO_DATA_OCCURRENCES = 5


def _fail(reason: str, detail: str = "") -> None:
    """Abort the run with a loud, greppable diagnostic."""
    logger.error("=" * 72)
    logger.error(f"RESEARCH ABORTED: {reason}")
    if detail:
        logger.error(detail)
    logger.error("=" * 72)
    sys.exit(1)


async def main():
    # 1. Initialize logging
    setup_logging()
    logger.info("Initializing local research for Sephora...")

    # 2. Get the research service
    service = get_research_service()

    # 3. Create a unique job ID
    job_id = service.new_job_id()
    company_name = "Sephora"
    metadata = {
        "user_id": "sephora-test@colt.net",
        "username": "Sephora E2E Test",
        "business_unit": "Sales",
        "organization": "Colt",
    }

    logger.info(f"Generated job ID: {job_id}")

    # 4. Create the research request in BigQuery
    logger.info("Creating research request in BigQuery...")
    success = service.create_research_request(
        job_id=job_id, company_name=company_name, metadata=metadata
    )
    if not success:
        _fail("Could not create research request in BigQuery.")

    logger.info("Research request created successfully in BigQuery.")

    # 5. Process the research job (runs the full ADK pipeline).
    #    The domain-output gate in the agent callbacks raises
    #    RESEARCH_DATA_MISSING (non-retryable) the moment ResearchSynthesizer
    #    finishes without populating the per-domain state keys, so an empty
    #    research phase surfaces here instead of becoming an empty report.
    logger.info("Starting research processing...")
    try:
        await service.process_research_background(
            job_id=job_id, company_name=company_name, metadata=metadata
        )
        logger.info("Research pipeline execution complete.")
    except Exception as e:
        logger.exception(f"An error occurred during research execution: {e}")
        _fail(
            "Pipeline raised before completing.",
            (
                "If the message mentions 'domain outputs', the research phase "
                "produced no usable per-domain data -- the same gap that made the "
                "previous report read 'Data not available from research.'. Check "
                "the log for '[ResearchSynthesizer] Persisted N/12 domain output "
                "keys' and '[Gate]' lines to see what was recovered."
            ),
        )

    # 6. Fetch the results
    logger.info("Fetching research results from BigQuery/GCS...")
    result = service.get_request_result(job_id)
    if not result:
        _fail("Could not retrieve research results from BigQuery.")

    logger.info(f"Status: {result.get('status')}")
    logger.info(f"Download URL: {result.get('download_url')}")
    logger.info(f"Model Card: {result.get('model_card')}")

    # 7. Post-run guard: even if every gate passed, refuse to call a report
    #    successful when it is mostly "Data not available from research."
    report_content = result.get("report_content", "")
    if not report_content:
        _fail("No report content found in the results.")

    no_data_count = report_content.count(NO_DATA_MARKER)
    logger.info(
        f"Report length={len(report_content)} chars, "
        f"'{NO_DATA_MARKER}' occurrences={no_data_count}"
    )
    if no_data_count > MAX_NO_DATA_OCCURRENCES:
        _fail(
            f"Report contains {no_data_count} 'Data not available' sections "
            f"(threshold {MAX_NO_DATA_OCCURRENCES}).",
            (
                "The pipeline completed but the compiler had little to work with. "
                "Grep the log for '[ResearchSynthesizer] Persisted' and '[Gate]' to "
                "see how many of the 12 domain outputs were actually written."
            ),
        )

    logger.info("Research job completed successfully.")
    logger.info("Report snippet (first 500 characters):")
    logger.info("\n" + "=" * 50 + "\n" + report_content[:500] + "...\n" + "=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
