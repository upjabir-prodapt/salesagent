from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.shared.exceptions import AgentOutputError
from src.worker.runtime.state_mutation import (
    StoredSessionStateAdapter,
    is_mutable_state,
    mutate_stored_session_state,
    requires_cold_retry,
    state_remove,
)


class _FakeState:
    def __init__(self) -> None:
        self._value: dict = {"key": "value"}
        self._delta: dict = {"key": "delta"}

    def get(self, key: str, default=None):
        return self._value.get(key, default)

    def __setitem__(self, key: str, value) -> None:
        self._value[key] = value


def test_state_remove_dict_and_wrapper() -> None:
    plain: dict = {"a": 1}
    state_remove(plain, "a")
    assert plain == {}

    wrapped = _FakeState()
    state_remove(wrapped, "key")
    assert "key" not in wrapped._value
    assert "key" not in wrapped._delta

    state_remove(None, "x")


def test_is_mutable_state() -> None:
    assert is_mutable_state({"k": "v"}) is True
    assert is_mutable_state(None) is False


def test_requires_cold_retry_paths() -> None:
    assert requires_cold_retry(RuntimeError("contents are required")) is True

    exc = AgentOutputError(
        "missing",
        agent_name="ReportCompiler",
        output_key="final_report",
        error_class="REPORT_VALIDATION_FAILED",
    )
    assert requires_cold_retry(exc) is False

    with patch(
        "src.worker.runtime.state_mutation.retry_scope_for_error_class",
        return_value="RUNNER_COLD",
    ):
        cold_exc = AgentOutputError(
            "quota",
            agent_name="MarketAgent",
            output_key="marketagent_output",
            error_class="RESOURCE_EXHAUSTED",
        )
        assert requires_cold_retry(cold_exc) is True


def test_stored_session_state_adapter_mutates_in_memory_service() -> None:
    from types import SimpleNamespace

    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    service = InMemorySessionService()
    stored_state: dict = {"retry_count": 0}
    session = SimpleNamespace(id="sess-1", state=stored_state)
    service.sessions = {"app": {"user": {"sess-1": session}}}

    adapter = StoredSessionStateAdapter(
        service, app_name="app", user_id="user", session_id="sess-1"
    )

    def mutator(state: dict) -> None:
        state["retry_count"] = 1

    assert adapter.mutate(mutator) is True
    assert stored_state["retry_count"] == 1


def test_stored_session_state_adapter_unsupported_service() -> None:
    adapter = StoredSessionStateAdapter(
        MagicMock(), app_name="app", user_id="user", session_id="sess"
    )
    assert adapter.mutate(lambda s: None) is False


def test_mutate_stored_session_state_wrapper() -> None:
    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    service = InMemorySessionService()
    stored_state: dict = {"flag": False}
    session = SimpleNamespace(id="sess-2", state=stored_state)
    service.sessions = {"app2": {"user2": {"sess-2": session}}}

    ok = mutate_stored_session_state(
        service,
        app_name="app2",
        user_id="user2",
        session_id="sess-2",
        mutator=lambda state: state.update({"flag": True}),
    )

    assert ok is True
    assert stored_state["flag"] is True
