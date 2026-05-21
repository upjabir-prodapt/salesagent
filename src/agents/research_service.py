"""Research Service - Business Logic for Research Operations"""

import asyncio
import io
import time
from typing import Any

from google.adk.agents.run_config import RunConfig
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from .session_service_factory import build_session_service
from google.genai import types
from opentelemetry import trace
from opentelemetry.propagate import extract
from tenacity import AsyncRetrying, stop_after_attempt, wait_fixed

from ..core.config import settings
from ..core.exceptions import ServiceError
from ..core.logging_config import contextualize, logger
from ..repositories.bigquery_repository import BigQueryRepository
from ..repositories.gcs_repository import GCSRepository
from ..utils.agent import log_event
from ..utils.guardrails import AgentGuardrail, OutputGuardrail
from ..utils.telemetry import TELEMETRY_RECORDS_KEY
from .evaluation_service import EvaluationService
from .salesAgent.agent import create_sales_agent_app

tracer = trace.get_tracer(__name__)


class ResearchService:
    """Service for handling research operations"""

    def __init__(
        self,
        bigquery_repository: BigQueryRepository,
        gcs_repository: GCSRepository,
    ):
        self.bigquery_repo = bigquery_repository
        self.gcs_repo = gcs_repository

    def create_research_request(
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
        self,
        job_id: str,
        company_name: str,
        metadata: dict | None = None,
        trace_context_headers: dict[str, str] | None = None,
    ) -> None:
        """Process research request in background using SalesAgent"""
        parent_context = (
            extract(carrier=trace_context_headers) if trace_context_headers else None
        )

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

        with tracer.start_as_current_span(
            "research.background.process",
            context=parent_context,
        ) as span:
            span.set_attribute("research.job_id", job_id)
            span.set_attribute("research.company_name", company_name)
            span.set_attribute("research.has_metadata", bool(metadata))
            span.set_attribute("research.status", "started")
            with contextualize(**context_metadata):
                try:
                    logger.info(
                        f"Starting research for job {job_id}: {company_name}"
                    )

                    self.bigquery_repo.update_status(
                        job_id,
                        "PROCESSING",
                        progress=settings.RESEARCH_INIT_PROGRESS,
                        current_step=settings.RESEARCH_INIT_STEP_LABEL,
                    )

                    start_time = time.monotonic()

                    # 1. Execute the main research loop with guardrails
                    final_report, session_state = await self._run_research_loop(
                        job_id, company_name
                    )
                    if not final_report:
                        return  # Early exit if loop failed and handled its own status update

                    latency = round(time.monotonic() - start_time, 2)
                    span.set_attribute("research.latency_seconds", latency)

                    # 2. Extract metrics and telemetry
                    metrics = self._calculate_metrics(session_state, latency)
                    if metrics["total_tokens"]:
                        span.set_attribute(
                            "research.total_tokens", int(metrics["total_tokens"])
                        )
                    if metrics["cost_usd"] is not None:
                        span.set_attribute(
                            "research.cost_usd", float(metrics["cost_usd"])
                        )

                    # 2b. Cost reconciliation vs per-agent telemetry
                    reconciliation = self._reconcile_cost(session_state, metrics)

                    # 3. Upload artifacts (final report + session state)
                    md_uri = self._upload_artifacts(
                        job_id, final_report, session_state
                    )

                    # 3b. Upload per-agent output artifacts
                    try:
                        await self._upload_agent_artifacts(job_id, session_state)
                    except Exception as e:
                        logger.warning(f"Per-agent artifact upload failed: {e}")

                    # 4. Finalize side operations (PDF, Evaluation, Cost, Telemetry)
                    (
                        side_op_failures,
                        pdf_available,
                    ) = await self._finalize_background_ops(
                        job_id, final_report, session_state, metrics
                    )

                    # 5. Mark COMPLETED
                    self._mark_completed(
                        job_id,
                        md_uri,
                        latency,
                        metrics,
                        pdf_available,
                        side_op_failures,
                        reconciliation=reconciliation,
                    )
                    span.set_attribute("research.status", "completed")
                except Exception as e:
                    self._handle_failure(e, job_id, span)
                    raise

    def _upload_artifacts(
        self, job_id: str, final_report: str, session_state: dict
    ) -> str:
        """Upload artifacts to GCS and update status."""
        logger.info(f"Uploading artifacts to GCS for job {job_id}")
        self.bigquery_repo.update_status(
            job_id,
            "PROCESSING",
            progress=settings.RESEARCH_UPLOAD_PROGRESS,
            current_step=settings.RESEARCH_UPLOAD_STEP_LABEL,
        )
        self.gcs_repo.upload_json(job_id, session_state)
        return self.gcs_repo.upload_markdown(job_id, final_report)

    def _mark_completed(
        self,
        job_id: str,
        md_uri: str,
        latency: float,
        metrics: dict,
        pdf_available: bool,
        side_op_failures: dict,
        reconciliation: dict | None = None,
    ) -> None:
        """Mark job as COMPLETED in the database."""
        meta: dict[str, Any] = {
            "model_version": settings.GEMINI_MODEL,
            "latency_seconds": latency,
            "tokens_used": metrics["total_tokens"] or None,
            "cost_usd": metrics["cost_usd"],
            "pdf_available": pdf_available,
            "side_op_failures": side_op_failures or None,
            "current_agent": None,
        }
        if reconciliation:
            meta["cost_reconciliation"] = reconciliation
        self.bigquery_repo.update_status(
            job_id,
            "COMPLETED",
            gcs_uri=md_uri,
            progress=100,
            current_step="Completed",
            metadata_update=meta,
        )
        logger.info(f"Research completed successfully for job {job_id}")

    def _handle_failure(self, e: Exception, job_id: str, span: Any) -> None:
        """Handle failure during research processing."""
        error_msg = str(e)
        if "GeneratorExit" in error_msg or "TaskGroup" in error_msg:
            error_msg = "Parallel execution collapsed (likely Quota/QPM limit reached)"
        span.record_exception(e)
        span.set_attribute("research.status", "failed")
        logger.error(f"Error processing research for job {job_id}: {e}")
        self.bigquery_repo.update_status(
            job_id,
            "FAILED",
            error=error_msg,
            metadata_update={"raw_error": str(e)[:1000]},
        )

    async def _run_research_loop(
        self, job_id: str, company_name: str
    ) -> tuple[str | None, dict]:
        """Execute SalesAgent with blocking output guardrail gate (retry on failure)"""
        max_retries = settings.OUTPUT_GUARDRAIL_MAX_RETRIES
        all_violations = []
        final_report: str | None = None
        session_state: dict = {}

        for attempt in range(max_retries + 1):
            if attempt > 0:
                logger.info(
                    f"[OutputGuardrail] Retry {attempt}/{max_retries} for job {job_id}"
                )

            final_report, session_state = await self._run_sales_agent(
                job_id, company_name, attempt=attempt
            )

            # Aggregate raw search cache and persist back to session_state
            # (EvaluationService and OutputGuardrail prefer a centralized cache key)
            raw_search_cache = []
            for k, v in session_state.items():
                if k.startswith("raw_search_cache_") and isinstance(v, list):
                    raw_search_cache.extend(v)

            session_state["raw_search_cache"] = raw_search_cache

            output_validation = await OutputGuardrail().validate(
                final_report, raw_search_cache=raw_search_cache
            )

            if output_validation.is_valid:
                return final_report, session_state

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
                return None, session_state

        return final_report, session_state

    def _calculate_metrics(self, session_state: dict, latency: float) -> dict:
        """Extract and calculate model card metrics from session state"""
        input_tokens = session_state.get("mc_input_tokens") or 0
        output_tokens = session_state.get("mc_output_tokens") or 0
        total_tokens = input_tokens + output_tokens

        cost_usd = None
        if (
            settings.GEMINI_COST_PER_1K_INPUT_TOKENS
            or settings.GEMINI_COST_PER_1K_OUTPUT_TOKENS
        ):
            cost_usd = round(
                (input_tokens / 1000) * settings.GEMINI_COST_PER_1K_INPUT_TOKENS
                + (output_tokens / 1000) * settings.GEMINI_COST_PER_1K_OUTPUT_TOKENS,
                6,
            )

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "latency": latency,
            "cost_usd": cost_usd,
            "temperature": session_state.get("mc_temperature"),
            "source_domains": session_state.get("mc_source_domains") or [],
        }

    async def _finalize_background_ops(
        self, job_id: str, final_report: str, session_state: dict, metrics: dict
    ) -> tuple[dict, bool]:
        """Run non-fatal side operations (PDF, Eval, Cost, Telemetry)"""
        side_op_failures = {}
        pdf_available = False

        # 1. PDF Generation
        async def _pdf_op():
            nonlocal pdf_available
            pdf_bytes = await asyncio.to_thread(self._generate_pdf_static, final_report)
            await asyncio.to_thread(self.gcs_repo.upload_pdf, job_id, pdf_bytes)
            pdf_available = True

        try:
            await self._with_retry(_pdf_op)
        except Exception as e:
            logger.warning(f"PDF op failed: {e}")
            side_op_failures["pdf"] = str(e)

        # 2. Evaluation
        async def _eval_op():
            self.bigquery_repo.update_status(
                job_id,
                "PROCESSING",
                progress=settings.RESEARCH_EVAL_PROGRESS,
                current_step=settings.RESEARCH_EVAL_STEP_LABEL,
            )
            result = await EvaluationService().evaluate(
                job_id, final_report, session_state
            )
            self.gcs_repo.upload_evaluation(job_id, result)

        try:
            await self._with_retry(_eval_op)
        except Exception as e:
            logger.warning(f"Eval op failed: {e}")
            side_op_failures["evaluation"] = str(e)

        # 3. Cost Attribution + reconciliation
        try:
            reconciliation = self._reconcile_cost(session_state, metrics)
            await self._with_retry_sync(
                lambda: self.bigquery_repo.insert_cost_attribution(
                    job_id=job_id,
                    model_version=settings.GEMINI_MODEL,
                    temperature=metrics["temperature"],
                    prompt_template_version=settings.PROMPT_TEMPLATE_VERSION,
                    input_tokens=metrics["input_tokens"] or None,
                    output_tokens=metrics["output_tokens"] or None,
                    total_tokens=metrics["total_tokens"] or None,
                    latency_seconds=metrics["latency"],
                    source_domains=metrics["source_domains"] or None,
                    cost_usd=metrics["cost_usd"],
                )
            )
        except Exception as e:
            logger.warning(f"Cost op failed: {e}")
            side_op_failures["cost_attribution"] = str(e)

        # 4. Telemetry
        telemetry_records = session_state.get(TELEMETRY_RECORDS_KEY) or []
        if telemetry_records:
            try:
                await self._with_retry_sync(
                    lambda: self.bigquery_repo.insert_agent_telemetry_batch(
                        telemetry_records
                    )
                )
            except Exception as e:
                logger.warning(f"Telemetry op failed: {e}")
                side_op_failures["telemetry"] = str(e)
                import contextlib

                with contextlib.suppress(Exception):
                    self.gcs_repo.upload_json(
                        f"{job_id}_telemetry_deadletter",
                        {"records": telemetry_records, "error": str(e)},
                    )

        return side_op_failures, pdf_available

    async def _run_sales_agent(
        self, job_id: str, company_name: str, attempt: int = 0
    ) -> tuple[str, dict]:
        """Run the SalesAgent and return the final report and session state"""
        with tracer.start_as_current_span("research.adk.run") as span:
            span.set_attribute("research.job_id", job_id)
            span.set_attribute("research.company_name", company_name)
            span.set_attribute("research.retry_attempt", attempt)
            app = create_sales_agent_app()
            session_service = build_session_service()
            runner = Runner(
                app=app,
                artifact_service=InMemoryArtifactService(),
                session_service=session_service,
                memory_service=InMemoryMemoryService(),
            )

            # 1. Initialize session
            session = await self._get_or_create_runner_session(
                runner, app, job_id, company_name, attempt
            )

            # Persist ADK session id for status tracking
            try:
                self.bigquery_repo.update_status(
                    job_id, None,
                    metadata_update={"adk_session_id": session.id},
                )
            except Exception as e:
                logger.debug(f"Could not persist adk_session_id: {e}")

            # 2. Run agents and handle events
            await self._handle_agent_run(runner, session, job_id, company_name, app)

            # 3. Fetch and validate final report
            session = await runner.session_service.get_session(
                app_name=app.name, user_id="api_user", session_id=session.id
            )
            final_report = session.state.get("final_report", "")

            if not final_report:
                raise ServiceError("No final report generated by SalesAgent")

            logger.info(
                f"Retrieved final report for job {job_id}, length: {len(final_report)}"
            )
            span.set_attribute("research.final_report_length", len(final_report))
            return final_report, session.state

    async def _get_or_create_runner_session(
        self, runner: Runner, app: Any, job_id: str, company_name: str, attempt: int
    ):
        """Helper to get or create a runner session"""
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
        return session

    async def _handle_agent_run(
        self, runner: Runner, session: Any, job_id: str, company_name: str, app: Any
    ):
        """Helper to execute the agent run and handle streaming events"""
        run_config = RunConfig()
        last_invocation_id = None

        # Build dynamic progress map
        all_agents = self._get_all_agents(app.root_agent)
        total_agents = len(all_agents)
        agent_descriptions = {a.name: a.description for a in all_agents}
        completed_agents: set[str] = set()

        status_write_state = {
            "last_progress": None,
            "last_step": None,
            "last_write_ts": 0.0,
            "seen_authors": set(),
        }

        try:
            with tracer.start_as_current_span("research.adk.runner.lifecycle") as span:
                span.set_attribute("research.job_id", job_id)
                span.set_attribute("research.company_name", company_name)
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
                            span.set_attribute("research.adk.resumed", True)
                        else:
                            run_kwargs["new_message"] = types.UserContent(
                                parts=[
                                    types.Part(
                                        text=f"Run the Sales Research Agent for the company {company_name}"
                                    )
                                ]
                            )
                            span.set_attribute("research.adk.resumed", False)

                        async for event in runner.run_async(**run_kwargs):
                            last_invocation_id = event.invocation_id
                            log_event(event, verbose=True)

                            # Hard-fail on agent error events
                            error_code = getattr(event, "error_code", None)
                            error_message = getattr(event, "error_message", None)
                            if error_code or error_message:
                                author = getattr(event, "author", "unknown_agent")
                                err_detail = error_message or f"error_code={error_code}"
                                raise ServiceError(
                                    f"Agent '{author}' failed: {err_detail}"
                                )

                            self._process_event_milestones(
                                event,
                                job_id,
                                total_agents,
                                completed_agents,
                                agent_descriptions,
                                status_write_state=status_write_state,
                            )

        except Exception as e:
            logger.error(f"Final failure after retries: {e}")
            raise

    def _get_all_agents(self, agent: Any) -> list[Any]:
        agents = [agent]
        if hasattr(agent, "sub_agents") and agent.sub_agents:
            for sub in agent.sub_agents:
                agents.extend(self._get_all_agents(sub))
        return agents

    def _process_event_milestones(
        self,
        event: Any,
        job_id: str,
        total_agents: int,
        completed_agents: set[str],
        agent_descriptions: dict[str, str],
        status_write_state: dict[str, Any],
    ):
        """Handle individual agent events and BQ progress updates"""
        if not hasattr(event, "author") or not event.author:
            return

        author = event.author
        is_final = getattr(event, "is_final_response", lambda: False)()

        # Detect new agent starting (first event from this author)
        seen_authors: set[str] = status_write_state.setdefault("seen_authors", set())
        if (
            author not in seen_authors
            and author not in completed_agents
            and author not in ("user",)
        ):
            seen_authors.add(author)
            try:
                self.bigquery_repo.update_status(
                    job_id,
                    None,
                    current_step=f"Running: {author}",
                    metadata_update={"current_agent": author},
                )
            except Exception as e:
                logger.debug(f"Failed to write agent-start status for {author}: {e}")

        # Validate agent output on completion
        if is_final and hasattr(event, "response") and event.response:
            agent_text = getattr(event.response, "text", "")
            if agent_text:
                try:
                    AgentGuardrail().validate(agent_text, agent_name=author)
                except Exception as guard_err:
                    logger.error(
                        f"[AgentGuardrail] Violation in {author}: {guard_err}"
                    )
                    raise

        # Update progress milestones
        if is_final:
            completed_agents.add(author)
            pct = int((len(completed_agents) / total_agents) * 100)
            # Ensure it doesn't exceed 99% here, 100% is reserved for COMPLETED status
            pct = min(pct, 99)

            description = agent_descriptions.get(author, "")
            label = (
                f"{author}: {description}"
                if description
                else f"{author} completed"
            )

            if self._should_write_status_update(
                status_write_state,
                progress=pct,
                current_step=label,
            ):
                self.bigquery_repo.update_status(
                    job_id,
                    None,
                    progress=pct,
                    current_step=label,
                )

    def _should_write_status_update(
        self,
        status_write_state: dict[str, Any],
        *,
        progress: int | None,
        current_step: str | None,
    ) -> bool:
        """Debounce repeated status writes to BigQuery."""
        now = time.monotonic()
        same_payload = (
            status_write_state.get("last_progress") == progress
            and status_write_state.get("last_step") == current_step
        )
        min_interval = settings.RESEARCH_STATUS_MIN_UPDATE_INTERVAL_SECONDS
        recently_written = (
            now - float(status_write_state.get("last_write_ts", 0.0))
        ) < min_interval
        if same_payload and recently_written:
            return False
        status_write_state["last_progress"] = progress
        status_write_state["last_step"] = current_step
        status_write_state["last_write_ts"] = now
        return True

    def get_request_status(self, job_id: str) -> dict[str, Any] | None:
        """Get the current status of a research job"""
        try:
            return self.bigquery_repo.get_status(job_id)
        except Exception as e:
            logger.error(f"Failed to get job status: {e}")
            raise ServiceError(f"Failed to get job status: {str(e)}") from e

    def get_pdf_report(self, job_id: str) -> tuple[bytes, str] | None:
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

    def get_request_result(self, job_id: str) -> dict[str, Any] | None:
        """Get the result of a completed research job, including cost attribution"""
        try:
            result = self.bigquery_repo.get_request_result(
                job_id, gcs_repository=self.gcs_repo
            )
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
            "all_violations": [
                {"rule": v.rule, "detail": v.detail} for v in violations
            ],
        }

    async def _upload_agent_artifacts(
        self, job_id: str, session_state: dict
    ) -> dict[str, str]:
        """Upload each agent's output artifact to GCS under the exact path.

        Path: salesagent_response/<session_id>/artifacts/<agent_name>_output.json
        """
        session_id = f"api_request_{job_id}"
        agent_output_keys = [k for k in session_state if k.endswith("_output")]
        uris: dict[str, str] = {}
        for key in agent_output_keys:
            content = session_state.get(key)
            if not content:
                continue
            agent_name = key[: -len("_output")]
            try:
                payload = (
                    content if isinstance(content, str) else str(content)
                )
                uri = await asyncio.to_thread(
                    self.gcs_repo.upload_agent_artifact,
                    session_id,
                    agent_name,
                    payload,
                )
                uris[key] = uri
            except Exception as e:
                logger.warning(f"Failed to upload artifact for {agent_name}: {e}")
        if uris:
            logger.info(
                f"Uploaded {len(uris)} per-agent artifacts for job {job_id}"
            )
        return uris

    def _reconcile_cost(self, session_state: dict, metrics: dict) -> dict:
        """Cross-check session-level cost against per-agent telemetry aggregation."""
        telemetry_records = session_state.get("agent_telemetry_records") or []
        per_agent_input = sum(r.get("tokens_input") or 0 for r in telemetry_records)
        per_agent_output = sum(r.get("tokens_output") or 0 for r in telemetry_records)
        per_agent_cost = sum(r.get("cost_usd") or 0.0 for r in telemetry_records)

        session_input = metrics.get("input_tokens") or 0
        session_output = metrics.get("output_tokens") or 0
        session_cost = metrics.get("cost_usd") or 0.0

        delta_input = abs(session_input - per_agent_input)
        delta_output = abs(session_output - per_agent_output)
        delta_cost = abs(session_cost - per_agent_cost)

        reconciliation = {
            "session_input_tokens": session_input,
            "session_output_tokens": session_output,
            "per_agent_input_tokens": per_agent_input,
            "per_agent_output_tokens": per_agent_output,
            "delta_input_tokens": delta_input,
            "delta_output_tokens": delta_output,
            "per_agent_cost_usd": round(per_agent_cost, 6),
            "session_cost_usd": round(session_cost, 6),
            "delta_cost_usd": round(delta_cost, 6),
            "agent_record_count": len(telemetry_records),
        }

        if delta_cost > 0.10 or delta_input > 5000 or delta_output > 5000:
            logger.warning(
                "[CostReconciliation] Notable discrepancy — "
                "session_tokens=%d per_agent_tokens=%d delta_cost=$%.4f",
                session_input + session_output,
                per_agent_input + per_agent_output,
                delta_cost,
            )
        else:
            logger.info(
                "[CostReconciliation] OK — session_tokens=%d per_agent_tokens=%d "
                "delta_cost=$%.6f agent_records=%d",
                session_input + session_output,
                per_agent_input + per_agent_output,
                delta_cost,
                len(telemetry_records),
            )
        return reconciliation

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
        return await self._with_retry(
            lambda: asyncio.to_thread(fn), retries=retries, delay=delay
        )
