"""Research Service - Business Logic for Research Operations"""

import io
import time
from typing import Any

from google.adk.agents.run_config import RunConfig
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types
from loguru import logger
from tenacity import AsyncRetrying, stop_after_attempt, wait_fixed

from ..core.config import settings
from ..core.exceptions import OutputValidationException, ServiceError
from ..repositories.bigquery_repository import BigQueryRepository
from ..repositories.firestore_repository import FirestoreRepository
from ..repositories.gcs_repository import GCSRepository
from ..utils.agent import log_event
from ..utils.guardrails import OutputGuardrail
from ..utils.telemetry import TELEMETRY_RECORDS_KEY
from .evaluation_service import EvaluationService
from .salesAgent.agent import create_sales_agent_app


class ResearchService:
    """Service for handling research operations"""

    def __init__(
        self,
        bigquery_repository: BigQueryRepository,
        gcs_repository: GCSRepository,
        firestore_repository: FirestoreRepository,
    ):
        self.bigquery_repo = bigquery_repository
        self.gcs_repo = gcs_repository
        self.firestore_repo = firestore_repository

    async def create_research_request(
        self, job_id: str, company_name: str, metadata: dict[str, Any] | None = None
    ) -> bool:
        """Create a new research job in the database"""
        try:
            return self.bigquery_repo.create_request(
                job_id=job_id, company_name=company_name, metadata=metadata
            )
        except Exception as e:
            logger.error(f"Failed to create research request: {e}")
            raise ServiceError(f"Failed to create research request: {str(e)}") from e

    async def process_research_background(self, job_id: str, company_name: str) -> None:
        """Process research request in background using SalesAgent"""
        try:
            logger.info(f"Starting research for job {job_id}: {company_name}")

            # Initialize Firestore tracking
            await self.firestore_repo.initialize_job(job_id)
            await self.firestore_repo.update_overall_progress(
                job_id, settings.RESEARCH_INIT_PROGRESS, "PROCESSING"
            )

            self.bigquery_repo.update_status(
                job_id,
                "PROCESSING",
                progress=settings.RESEARCH_INIT_PROGRESS,
                current_step=settings.RESEARCH_INIT_STEP_LABEL,
            )

            start_time = time.monotonic()

            # Run the SalesAgent with blocking output guardrail gate (retry on failure)
            max_retries = settings.OUTPUT_GUARDRAIL_MAX_RETRIES
            final_report: str | None = None
            session_state: dict = {}

            for attempt in range(max_retries + 1):
                if attempt > 0:
                    logger.info(
                        f"[OutputGuardrail] Retry {attempt}/{max_retries} for job {job_id}"
                    )
                final_report, session_state = await self._run_sales_agent(
                    job_id, company_name
                )
                raw_search_cache = session_state.get("raw_search_cache") or []
                output_validation = await OutputGuardrail().validate(
                    final_report, raw_search_cache=raw_search_cache
                )
                if output_validation.is_valid:
                    break
                violation_details = "; ".join(
                    f"{v.rule}: {v.detail}" for v in output_validation.violations
                )
                if attempt < max_retries:
                    logger.warning(
                        f"[OutputGuardrail] Attempt {attempt + 1}/{max_retries + 1} "
                        f"blocked for job {job_id}: {violation_details} — retrying"
                    )
                else:
                    logger.error(
                        f"[OutputGuardrail] All {max_retries + 1} attempt(s) blocked "
                        f"for job {job_id}: {violation_details}"
                    )
                    raise OutputValidationException(
                        message=f"Output blocked after {max_retries + 1} attempt(s): {violation_details}",
                        violations=[v.rule for v in output_validation.violations],
                    )

            latency = round(time.monotonic() - start_time, 2)

            # Extract model card telemetry accumulated in session state by callbacks
            mc_input_tokens = session_state.get("mc_input_tokens") or 0
            mc_output_tokens = session_state.get("mc_output_tokens") or 0
            mc_total_tokens = mc_input_tokens + mc_output_tokens
            mc_temperature = session_state.get("mc_temperature")
            mc_source_domains = session_state.get("mc_source_domains") or []
            mc_cost_usd = (
                round(
                    (mc_input_tokens / 1000) * settings.GEMINI_COST_PER_1K_INPUT_TOKENS
                    + (mc_output_tokens / 1000)
                    * settings.GEMINI_COST_PER_1K_OUTPUT_TOKENS,
                    6,
                )
                if (
                    settings.GEMINI_COST_PER_1K_INPUT_TOKENS
                    or settings.GEMINI_COST_PER_1K_OUTPUT_TOKENS
                )
                else None
            )

            # Upload artifacts
            logger.info(f"Uploading artifacts to GCS for job {job_id}")
            self.bigquery_repo.update_status(
                job_id,
                "PROCESSING",
                progress=settings.RESEARCH_UPLOAD_PROGRESS,
                current_step=settings.RESEARCH_UPLOAD_STEP_LABEL,
            )
            self.gcs_repo.upload_json(job_id, session_state)
            md_uri = self.gcs_repo.upload_markdown(job_id, final_report)

            # Generate and upload PDF
            try:
                from markdown_pdf import MarkdownPdf, Section

                logger.info(f"Generating PDF for job {job_id}")
                pdf = MarkdownPdf(toc_level=0)
                pdf.add_section(Section(final_report))
                pdf_buffer = io.BytesIO()
                pdf.save(pdf_buffer)
                pdf_uri = self.gcs_repo.upload_pdf(job_id, pdf_buffer.getvalue())
                logger.info(f"PDF uploaded successfully to {pdf_uri}")
            except Exception as pdf_err:
                logger.warning(
                    f"PDF generation or upload failed for job {job_id} (non-fatal): {pdf_err}"
                )

            # Run evaluation (non-fatal)
            try:
                logger.info(f"Running evaluation for job {job_id}")
                self.bigquery_repo.update_status(
                    job_id,
                    "PROCESSING",
                    progress=settings.RESEARCH_EVAL_PROGRESS,
                    current_step=settings.RESEARCH_EVAL_STEP_LABEL,
                )
                evaluation_service = EvaluationService()
                evaluation_result = await evaluation_service.evaluate(
                    request_id=job_id,
                    final_report=final_report,
                    session_state=session_state,
                )
                self.gcs_repo.upload_evaluation(job_id, evaluation_result)
                logger.info(
                    f"Evaluation complete for job {job_id} — "
                    f"Final Score: {evaluation_result.get('final_composite_score', 'N/A')}"
                )
            except Exception as eval_err:
                logger.warning(
                    f"Evaluation failed for job {job_id} (non-fatal): {eval_err}"
                )

            # Insert model card record (non-fatal)
            try:
                self.bigquery_repo.insert_model_card(
                    job_id=job_id,
                    model_version=settings.GEMINI_MODEL,
                    temperature=mc_temperature,
                    prompt_template_version=settings.PROMPT_TEMPLATE_VERSION,
                    input_tokens=mc_input_tokens or None,
                    output_tokens=mc_output_tokens or None,
                    total_tokens=mc_total_tokens or None,
                    latency_seconds=latency,
                    source_domains=mc_source_domains or None,
                    cost_usd=mc_cost_usd,
                )
            except Exception as mc_err:
                logger.warning(
                    f"Model card insertion failed for job {job_id} (non-fatal): {mc_err}"
                )

            # Flush per-agent telemetry records to BigQuery (non-fatal)
            try:
                telemetry_records = session_state.get(TELEMETRY_RECORDS_KEY) or []
                if telemetry_records:
                    self.bigquery_repo.insert_agent_telemetry_batch(telemetry_records)
            except Exception as tel_err:
                logger.warning(
                    f"Agent telemetry flush failed for job {job_id} (non-fatal): {tel_err}"
                )

            # Mark COMPLETED, persist model card data into metadata
            await self.firestore_repo.update_overall_progress(job_id, 100, "COMPLETED")
            self.bigquery_repo.update_status(
                job_id,
                "COMPLETED",
                gcs_uri=md_uri,
                progress=100,
                current_step="Completed",
                metadata_update={
                    "model_version": settings.GEMINI_MODEL,
                    "latency_seconds": latency,
                    "tokens_used": mc_total_tokens or None,
                    "cost_usd": mc_cost_usd,
                },
            )
            logger.info(f"Research completed successfully for job {job_id}")

        except Exception as e:
            logger.error(f"Error processing research for job {job_id}: {e}")
            await self.firestore_repo.update_overall_progress(job_id, 0, "FAILED")
            self.bigquery_repo.update_status(job_id, "FAILED", error=str(e))
            raise

    async def _run_sales_agent(
        self, job_id: str, company_name: str
    ) -> tuple[str, dict]:
        """Run the SalesAgent and return the final report and session state"""
        app = create_sales_agent_app()
        runner = Runner(
            app=app,
            artifact_service=InMemoryArtifactService(),
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
        )

        session_id = f"api_request_{job_id}"
        session = await runner.session_service.get_session(
            app_name=app.name, user_id="api_user", session_id=session_id
        )
        if not session:
            session = await runner.session_service.create_session(
                app_name=app.name,
                user_id="api_user",
                session_id=session_id,
                state={"company_name": company_name, "job_execution_id": job_id},
            )

        run_config = RunConfig()
        last_invocation_id = None
        progress_map = settings.agent_progress_map

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(settings.AGENT_RETRY_ATTEMPTS),
                wait=wait_fixed(settings.AGENT_RETRY_WAIT_FIXED),
                reraise=True,
            ):
                with attempt:
                    run_kwargs = {
                        "user_id": "api_user",
                        "session_id": session.id,
                        "run_config": run_config,
                    }

                    if last_invocation_id:
                        logger.info(f"\nResuming invocation: {last_invocation_id}")
                        run_kwargs["invocation_id"] = last_invocation_id
                    else:
                        run_kwargs["new_message"] = types.UserContent(
                            parts=[
                                types.Part(
                                    text=f"Run the Sales Research Agent for the company {company_name}"
                                )
                            ]
                        )

                    async for event in runner.run_async(**run_kwargs):
                        last_invocation_id = event.invocation_id
                        log_event(event, verbose=True)

                        # Update individual agent progress in Firestore
                        if hasattr(event, "author") and event.author:
                            # Use 50% for intermediate events, 100% when final response
                            is_final = getattr(
                                event, "is_final_response", lambda: False
                            )()
                            agent_status = "COMPLETED" if is_final else "PROCESSING"
                            agent_pct = 100 if is_final else 50
                            await self.firestore_repo.update_agent_progress(
                                job_id, event.author, agent_pct, agent_status
                            )

                        # Update progress based on agent milestones
                        if event.author in progress_map and event.is_final_response():
                            pct, label = progress_map[event.author]
                            self.bigquery_repo.update_status(
                                job_id, None, progress=pct, current_step=label
                            )
                            await self.firestore_repo.update_overall_progress(
                                job_id, pct, "PROCESSING"
                            )

        except Exception as e:
            logger.error(f"Final failure after retries: {e}")
            raise

        # Fetch final session state
        session = await runner.session_service.get_session(
            app_name=app.name, user_id="api_user", session_id=session_id
        )
        final_report = session.state.get("final_report", "")

        if not final_report:
            raise ServiceError("No final report generated by SalesAgent")

        logger.info(
            f"Retrieved final report for job {job_id}, length: {len(final_report)} characters"
        )
        return final_report, session.state

    async def get_request_status(self, job_id: str) -> dict[str, Any] | None:
        """Get the current status of a research job"""
        try:
            return self.bigquery_repo.get_status(job_id)
        except Exception as e:
            logger.error(f"Failed to get job status: {e}")
            raise ServiceError(f"Failed to get job status: {str(e)}") from e

    async def get_pdf_report(self, job_id: str) -> tuple[bytes, str] | None:
        """
        Return (pdf_bytes, company_name) for a COMPLETED job, or None if not found.
        Raises ServiceError if the job is not yet complete or PDF is missing from GCS.
        """
        try:
            status_data = self.bigquery_repo.get_status(job_id)
        except Exception as e:
            raise ServiceError(f"Failed to fetch job status: {str(e)}") from e

        if status_data is None:
            return None

        if status_data.get("status") != "COMPLETED":
            raise ServiceError(
                f"PDF not available — job status is '{status_data.get('status')}'",
                status_code=409,
            )

        try:
            pdf_bytes = self.gcs_repo.download_pdf(job_id)
        except Exception as e:
            raise ServiceError(f"Failed to download PDF from storage: {str(e)}") from e

        if pdf_bytes is None:
            raise ServiceError("PDF file not found in storage for this job")

        company_name = status_data.get("company_name", job_id)
        return pdf_bytes, company_name

    async def get_request_result(self, job_id: str) -> dict[str, Any] | None:
        """Get the result of a completed research job, including model card"""
        try:
            result = self.bigquery_repo.get_request_result(job_id)
            if result is None:
                return None

            # Build model_card from metadata persisted during processing
            meta = result.get("metadata") or {}
            result["model_card"] = {
                "model_version": meta.get("model_version"),
                "tokens_used": meta.get("tokens_used"),
                "latency_seconds": meta.get("latency_seconds"),
                "cost_usd": meta.get("cost_usd"),
            }
            return result
        except Exception as e:
            logger.error(f"Failed to get job result: {e}")
            raise ServiceError(f"Failed to get job result: {str(e)}") from e
