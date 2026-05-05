"""Research Service - Business Logic for Research Operations"""

import asyncio
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
from ..repositories.gcs_repository import GCSRepository
from ..utils.agent import log_event
from ..utils.guardrails import AgentGuardrail, OutputGuardrail
from ..utils.telemetry import TELEMETRY_RECORDS_KEY
from .evaluation_service import EvaluationService
from .salesAgent.agent import create_sales_agent_app


class ResearchService:
    """Service for handling research operations"""

    def __init__(
        self,
        bigquery_repository: BigQueryRepository,
        gcs_repository: GCSRepository,
    ):
        self.bigquery_repo = bigquery_repository
        self.gcs_repo = gcs_repository

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

    @staticmethod
    def _generate_pdf_static(final_report: str) -> bytes:
        """Synchronous helper for PDF generation (CPU-bound)"""
        from markdown_pdf import MarkdownPdf, Section
        pdf = MarkdownPdf(toc_level=0)
        pdf.add_section(Section(final_report))
        pdf_buffer = io.BytesIO()
        pdf.save(pdf_buffer)
        return pdf_buffer.getvalue()

    async def process_research_background(
        self, job_id: str, company_name: str, metadata: dict | None = None
    ) -> None:
        """Process research request in background using SalesAgent"""
        # Contextualize logger if metadata is provided
        context_metadata = {}
        if metadata:
            context_metadata = {
                "user_email": metadata.get("user_id"),
                "username": metadata.get("username"),
                "business_unit": metadata.get("business_unit"),
                "organization": metadata.get("organization"),
                "trace_id": job_id,
            }

        with logger.contextualize(**context_metadata):
            try:
                logger.info(f"Starting research for job {job_id}: {company_name}")

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

                all_violations = []
                for attempt in range(max_retries + 1):
                    if attempt > 0:
                        logger.info(
                            f"[OutputGuardrail] Retry {attempt}/{max_retries} for job {job_id}"
                        )
                    final_report, session_state = await self._run_sales_agent(
                        job_id, company_name, attempt=attempt
                    )
                    
                    # Aggregate raw search cache from all agents (to survive parallel merge clobbering)
                    raw_search_cache = []
                    for k, v in session_state.items():
                        if k.startswith("raw_search_cache_") and isinstance(v, list):
                            raw_search_cache.extend(v)
                    
                    output_validation = await OutputGuardrail().validate(
                        final_report, raw_search_cache=raw_search_cache
                    )
                    if output_validation.is_valid:
                        break
                    
                    all_violations.extend(output_validation.violations)
                    violation_details = "; ".join(
                        f"{v.rule}: {v.detail}" for v in output_validation.violations
                    )
                    if attempt < max_retries:
                        logger.warning(
                            f"[OutputGuardrail] Attempt {attempt + 1}/{max_retries + 1} "
                            f"blocked for job {job_id}: {violation_details} — retrying"
                        )
                    else:
                        failure_summary = self._build_failure_summary(all_violations)
                        logger.error(
                            f"[OutputGuardrail] All {max_retries + 1} attempt(s) blocked "
                            f"for job {job_id}. Dominant rule: {failure_summary['dominant_rule']}"
                        )
                        self.bigquery_repo.update_status(
                            job_id,
                            "FAILED",
                            error=f"Output blocked: {failure_summary['dominant_rule']}",
                            metadata_update={"failure_summary": failure_summary},
                        )
                        return  # hard stop

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
                side_op_failures = {}
                pdf_available = False

                async def _generate_and_upload_pdf():
                    nonlocal pdf_available
                    logger.info(f"Generating PDF for job {job_id}")
                    pdf_bytes = await asyncio.to_thread(self._generate_pdf_static, final_report)
                    pdf_uri = await asyncio.to_thread(self.gcs_repo.upload_pdf, job_id, pdf_bytes)
                    logger.info(f"PDF uploaded successfully to {pdf_uri}")
                    pdf_available = True

                try:
                    await self._with_retry(_generate_and_upload_pdf)
                except Exception as pdf_err:
                    logger.warning(
                        f"PDF generation or upload failed permanently for job {job_id}: {pdf_err}"
                    )
                    side_op_failures["pdf"] = str(pdf_err)

                # Run evaluation (non-fatal)
                async def _run_evaluation():
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

                try:
                    await self._with_retry(_run_evaluation)
                except Exception as eval_err:
                    logger.warning(
                        f"Evaluation failed permanently for job {job_id}: {eval_err}"
                    )
                    side_op_failures["evaluation"] = str(eval_err)

                # Insert cost attribution record (non-fatal)
                try:
                    await self._with_retry_sync(lambda: self.bigquery_repo.insert_cost_attribution(
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
                    ))
                except Exception as mc_err:
                    logger.warning(
                        f"Cost attribution insertion failed permanently for job {job_id}: {mc_err}"
                    )
                    side_op_failures["cost_attribution"] = str(mc_err)

                # Flush per-agent telemetry records to BigQuery (non-fatal)
                telemetry_records = session_state.get(TELEMETRY_RECORDS_KEY) or []
                if telemetry_records:
                    try:
                        await self._with_retry_sync(lambda: self.bigquery_repo.insert_agent_telemetry_batch(telemetry_records))
                    except Exception as tel_err:
                        logger.warning(
                            f"Agent telemetry flush failed permanently — writing dead-letter: {tel_err}"
                        )
                        side_op_failures["telemetry"] = str(tel_err)
                        try:
                            self.gcs_repo.upload_json(
                                f"{job_id}_telemetry_deadletter",
                                {"records": telemetry_records, "error": str(tel_err)},
                            )
                        except Exception as dl_err:
                            logger.error(f"Dead-letter write also failed for job {job_id}: {dl_err}")

                # Mark COMPLETED, persist cost attribution data into metadata
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
                        "pdf_available": pdf_available,
                        "side_op_failures": side_op_failures or None,
                    },
                )
                logger.info(f"Research completed successfully for job {job_id}")

            except Exception as e:
                # Handle parallel execution crashes (Common in large AstraZeneca-style runs)
                error_msg = str(e)
                if "GeneratorExit" in error_msg or "TaskGroup" in error_msg:
                    error_msg = "Parallel execution collapsed (likely Quota/QPM limit reached)"
                
                logger.error(f"Error processing research for job {job_id}: {e}")
                self.bigquery_repo.update_status(
                    job_id, 
                    "FAILED", 
                    error=error_msg,
                    metadata_update={"raw_error": str(e)[:1000]}
                )
                raise

    async def _run_sales_agent(
        self, job_id: str, company_name: str, attempt: int = 0
    ) -> tuple[str, dict]:
        """Run the SalesAgent and return the final report and session state"""
        app = create_sales_agent_app()
        runner = Runner(
            app=app,
            artifact_service=InMemoryArtifactService(),
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
        )

        # Append attempt number to session_id to ensure fresh execution on retries
        session_id = f"api_request_{job_id}"
        if attempt > 0:
            session_id += f"_retry_{attempt}"

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

                        # Update individual agent progress in BigQuery
                        if hasattr(event, "author") and event.author:
                            is_final = getattr(
                                event, "is_final_response", lambda: False
                            )()
                            agent_status = "COMPLETED" if is_final else "PROCESSING"
                            
                            # Log to BQ current_step for visibility
                            self.bigquery_repo.update_status(
                                job_id, 
                                None, 
                                current_step=f"Agent: {event.author} ({agent_status})"
                            )

                            # --- Iterative Guardrail: Validate agent output on completion ---
                            if is_final and hasattr(event, "response") and event.response:
                                try:
                                    # event.response is an AgentResponse object
                                    agent_text = getattr(event.response, "text", "")
                                    if agent_text:
                                        AgentGuardrail().validate(agent_text, agent_name=event.author)
                                except Exception as guard_err:
                                    logger.error(f"[AgentGuardrail] Violation in {event.author}: {guard_err}")
                                    raise

                        # Update progress based on agent milestones
                        if event.author in progress_map and event.is_final_response():
                            pct, label = progress_map[event.author]
                            self.bigquery_repo.update_status(
                                job_id, None, progress=pct, current_step=label
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
        """Get the result of a completed research job, including cost attribution"""
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

    def _build_failure_summary(self, violations: list) -> dict:
        """Construct a structured summary of guardrail violations."""
        from collections import Counter
        rule_counts = Counter(v.rule for v in violations)
        dominant_rule = rule_counts.most_common(1)[0][0] if rule_counts else "unknown"
        return {
            "dominant_rule": dominant_rule,
            "all_violations": [{"rule": v.rule, "detail": v.detail} for v in violations],
        }

    async def _with_retry(self, coro_fn, retries: int = 1, delay: float = 3.0):
        """Simple async retry wrapper."""
        for attempt in range(retries + 1):
            try:
                return await coro_fn()
            except Exception:
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    raise

    async def _with_retry_sync(self, fn, retries: int = 1, delay: float = 3.0):
        """Simple sync-to-thread retry wrapper."""
        return await self._with_retry(lambda: asyncio.to_thread(fn), retries=retries, delay=delay)
