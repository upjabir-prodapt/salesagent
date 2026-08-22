"""Script to run local research for Sephora programmatically without upping the FastAPI server."""

import asyncio
import sys
import os
from datetime import datetime

# Add the Sales-Agent directory to path so imports work correctly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.core.logging_config import setup_logging, logger
from src.dependencies.service_dependencies import get_research_service


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
        logger.error("Failed to create research request in BigQuery. Exiting.")
        sys.exit(1)

    logger.info("Research request created successfully in BigQuery.")

    # 5. Process the research job in the background (which runs the full ADK pipeline)
    logger.info("Starting background research processing...")
    try:
        await service.process_research_background(
            job_id=job_id, company_name=company_name, metadata=metadata
        )
        logger.info("Research pipeline execution complete.")
    except Exception as e:
        logger.exception(f"An error occurred during research execution: {e}")
        sys.exit(1)

    # 6. Fetch and print the results
    logger.info("Fetching research results from BigQuery/GCS...")
    result = service.get_request_result(job_id)
    if result:
        logger.info("Research Job execution was successful!")
        logger.info(f"Status: {result.get('status')}")
        logger.info(f"Download URL: {result.get('download_url')}")
        logger.info(f"Model Card: {result.get('model_card')}")
        logger.info("Report snippet (First 500 characters):")
        report_content = result.get("report_content", "")
        if report_content:
            logger.info("\n" + "=" * 50 + "\n" + report_content[:500] + "...\n" + "=" * 50)
        else:
            logger.warning("No report content found in the results.")
    else:
        logger.error("Could not retrieve research results from BigQuery.")


if __name__ == "__main__":
    asyncio.run(main())
