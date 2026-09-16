"""Unit tests for the shared tenacity retry engine (src/shared/retrying.py).

Every test injects its own ``sleep`` so the backoff is asserted on the
*computed* delays rather than measured wall-clock. That matters on
Windows, where the ProactorEventLoop quantises ``asyncio.sleep`` to
~15.6ms and a requested 0.001s actually sleeps 0-16ms -- measuring time
here would be both slow and flaky.
"""

from __future__ import annotations

import asyncio

import pytest

from src.shared.retrying import (
    backoff_delay,
    build_async_retrying,
    build_sync_retrying,
    retry_after_seconds,
    retry_async,
    retry_sync,
    wait_backoff,
)


class _FakeHeaders:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def get(self, key: str, default=None):
        return self._mapping.get(key, default)


class _FakeResponse:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = _FakeHeaders(headers)


class _ApiError(Exception):
    """Stands in for google.genai.errors.APIError."""

    def __init__(self, details=None, response=None, code: int = 429) -> None:
        super().__init__(f"{code} RESOURCE_EXHAUSTED. {details}")
        self.details = details
        self.response = response
        self.code = code


def _retry_info_body(delay: str) -> dict:
    """The shape Vertex actually returns for a quota error."""
    return {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": delay,
                }
            ],
        }
    }


class TestBackoffDelay:
    def test_geometric_without_jitter(self):
        kw = {"initial_delay": 1.0, "max_delay": 100.0, "jitter": 0.0}
        assert backoff_delay(1, **kw) == 1.0
        assert backoff_delay(2, **kw) == 2.0
        assert backoff_delay(3, **kw) == 4.0
        assert backoff_delay(4, **kw) == 8.0

    def test_capped_at_max_delay(self):
        assert backoff_delay(9, initial_delay=10.0, max_delay=15.0, jitter=0.0) == 15.0

    def test_jitter_is_symmetric_and_non_negative(self):
        """Symmetric jitter must be able to land *below* the raw delay.

        This is the property tenacity's own wait_exponential_jitter cannot
        provide (it only ever adds uniform(0, jitter)), and it is why the
        engine keeps its own wait strategy.
        """
        below = above = False
        for _ in range(200):
            delay = backoff_delay(3, initial_delay=1.0, max_delay=100.0, jitter=0.5)
            assert 0.0 <= delay <= 4.0 * 1.5 + 1e-9
            below = below or delay < 4.0
            above = above or delay > 4.0
        assert below and above

    def test_custom_exp_base(self):
        assert (
            backoff_delay(3, initial_delay=1.0, max_delay=99.0, exp_base=3.0, jitter=0)
            == 9.0
        )


class TestRetryAfterSeconds:
    def test_reads_google_rpc_retry_info(self):
        exc = _ApiError(details=_retry_info_body("34s"))
        assert retry_after_seconds(exc) == 34.0

    def test_reads_fractional_retry_info(self):
        assert retry_after_seconds(_ApiError(details=_retry_info_body("2.5s"))) == 2.5

    def test_reads_retry_after_header(self):
        exc = _ApiError(response=_FakeResponse({"Retry-After": "12"}))
        assert retry_after_seconds(exc) == 12.0

    def test_header_lookup_is_case_insensitive(self):
        exc = _ApiError(response=_FakeResponse({"retry-after": "7"}))
        assert retry_after_seconds(exc) == 7.0

    def test_retry_info_wins_over_header(self):
        exc = _ApiError(
            details=_retry_info_body("30s"),
            response=_FakeResponse({"Retry-After": "1"}),
        )
        assert retry_after_seconds(exc) == 30.0

    @pytest.mark.parametrize(
        "delay", ["0s", "-5s", "99999s", "soon", "", "not-a-duration"]
    )
    def test_unusable_hints_are_ignored(self, delay):
        """An absurd or unparseable hint must not become a wait."""
        assert retry_after_seconds(_ApiError(details=_retry_info_body(delay))) is None

    def test_no_hint_present(self):
        assert retry_after_seconds(Exception("plain failure")) is None
        assert retry_after_seconds(_ApiError(details={"unrelated": True})) is None

    def test_never_raises_on_hostile_input(self):
        """A malformed hint must never break the loop that is recovering."""

        class Hostile(Exception):
            @property
            def details(self):
                raise RuntimeError("boom")

        assert retry_after_seconds(Hostile()) is None


class TestWaitBackoff:
    def _state(self, attempt: int, exc: BaseException | None):
        class Outcome:
            failed = exc is not None

            def exception(self):
                return exc

        class State:
            attempt_number = attempt
            outcome = Outcome() if exc is not None else None

        return State()

    def test_falls_back_to_computed_backoff(self):
        wait = wait_backoff(initial_delay=2.0, max_delay=120.0, jitter=0.0)
        assert wait(self._state(1, Exception("no hint"))) == 2.0
        assert wait(self._state(3, Exception("no hint"))) == 8.0

    def test_server_hint_overrides_a_shorter_backoff(self):
        wait = wait_backoff(initial_delay=2.0, max_delay=120.0, jitter=0.0)
        exc = _ApiError(details=_retry_info_body("34s"))
        assert wait(self._state(1, exc)) == 34.0

    def test_never_waits_less_than_our_own_backoff(self):
        """A short Retry-After must not turn into a hammer loop."""
        wait = wait_backoff(initial_delay=2.0, max_delay=120.0, jitter=0.0)
        exc = _ApiError(details=_retry_info_body("3s"))
        assert wait(self._state(6, exc)) == 64.0

    def test_server_hint_is_capped_at_max_delay(self):
        wait = wait_backoff(initial_delay=1.0, max_delay=20.0, jitter=0.0)
        exc = _ApiError(details=_retry_info_body("200s"))
        assert wait(self._state(1, exc)) == 20.0

    def test_respect_retry_after_can_be_disabled(self):
        wait = wait_backoff(
            initial_delay=2.0, max_delay=120.0, jitter=0.0, respect_retry_after=False
        )
        exc = _ApiError(details=_retry_info_body("34s"))
        assert wait(self._state(1, exc)) == 2.0


async def _drive(retrying, body):
    """Run ``body`` under a tenacity async-for loop, returning its value."""
    result = None
    async for attempt in retrying:
        with attempt:
            result = await body()
        state = attempt.retry_state
        if state.outcome is not None and not state.outcome.failed:
            state.set_result(result)
    return result


class TestAsyncRetryLoop:
    @pytest.mark.asyncio
    async def test_recovers_and_backs_off_exponentially(self):
        slept: list[float] = []

        async def _sleep(seconds: float) -> None:
            slept.append(seconds)

        calls = {"n": 0}

        async def body():
            calls["n"] += 1
            if calls["n"] < 4:
                raise ConnectionError("429 RESOURCE_EXHAUSTED")
            return "ok"

        retrying = build_async_retrying(
            max_attempts=6,
            wait=wait_backoff(initial_delay=1.0, max_delay=60.0, jitter=0.0),
            should_retry=lambda exc, n: isinstance(exc, ConnectionError),
            sleep=_sleep,
        )
        assert await _drive(retrying, body) == "ok"
        assert calls["n"] == 4
        assert slept == [1.0, 2.0, 4.0]

    @pytest.mark.asyncio
    async def test_exhaustion_reraises_the_original_exception(self):
        """Never a tenacity.RetryError -- callers classify the real error."""
        slept: list[float] = []

        async def _sleep(seconds: float) -> None:
            slept.append(seconds)

        async def body():
            raise ConnectionError("429 RESOURCE_EXHAUSTED")

        retrying = build_async_retrying(
            max_attempts=3,
            wait=wait_backoff(initial_delay=1.0, max_delay=60.0, jitter=0.0),
            should_retry=lambda exc, n: True,
            sleep=_sleep,
        )
        with pytest.raises(ConnectionError):
            await _drive(retrying, body)
        assert slept == [1.0, 2.0]

    @pytest.mark.asyncio
    async def test_non_retryable_propagates_without_sleeping(self):
        slept: list[float] = []

        async def _sleep(seconds: float) -> None:
            slept.append(seconds)

        async def body():
            raise ValueError("fatal")

        retrying = build_async_retrying(
            max_attempts=5,
            wait=wait_backoff(initial_delay=1.0, max_delay=9.0, jitter=0.0),
            should_retry=lambda exc, n: isinstance(exc, ConnectionError),
            sleep=_sleep,
        )
        with pytest.raises(ValueError):
            await _drive(retrying, body)
        assert slept == []

    @pytest.mark.asyncio
    async def test_predicate_sees_the_attempt_number(self):
        """Per-kind attempt budgets need the attempt number, which
        tenacity's retry_if_exception cannot supply."""
        seen: list[int] = []

        async def _sleep(seconds: float) -> None:
            return None

        async def body():
            raise ConnectionError("boom")

        def should_retry(exc: BaseException, attempt: int) -> bool:
            seen.append(attempt)
            return attempt < 2

        retrying = build_async_retrying(
            max_attempts=9,
            wait=wait_backoff(initial_delay=0.0, max_delay=0.0, jitter=0.0),
            should_retry=should_retry,
            sleep=_sleep,
        )
        with pytest.raises(ConnectionError):
            await _drive(retrying, body)
        assert seen == [1, 2]

    @pytest.mark.asyncio
    async def test_max_elapsed_stops_the_loop(self):
        """The wall-clock budget must bound a large attempt budget."""

        async def body():
            raise ConnectionError("boom")

        retrying = build_async_retrying(
            max_attempts=1000,
            wait=wait_backoff(initial_delay=0.01, max_delay=0.01, jitter=0.0),
            should_retry=lambda exc, n: True,
            max_elapsed=0.05,
        )
        with pytest.raises(ConnectionError):
            await _drive(retrying, body)

    @pytest.mark.asyncio
    async def test_before_sleep_sees_the_upcoming_delay(self):
        observed: list[tuple[int, float]] = []

        async def _sleep(seconds: float) -> None:
            return None

        async def body():
            raise ConnectionError("boom")

        def hook(state):
            observed.append((state.attempt_number, state.next_action.sleep))

        retrying = build_async_retrying(
            max_attempts=3,
            wait=wait_backoff(initial_delay=1.0, max_delay=60.0, jitter=0.0),
            should_retry=lambda exc, n: True,
            before_sleep=hook,
            sleep=_sleep,
        )
        with pytest.raises(ConnectionError):
            await _drive(retrying, body)
        # Fires after every failed attempt except the last.
        assert observed == [(1, 1.0), (2, 2.0)]


class TestGeneralPurposeHelpers:
    @pytest.mark.asyncio
    async def test_retry_async_recovers(self):
        calls = {"n": 0}

        async def op():
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("transient")
            return 42

        got = await retry_async(
            op, max_attempts=5, initial_delay=0.0, max_delay=0.0, jitter=0.0
        )
        assert got == 42
        assert calls["n"] == 3

    @pytest.mark.asyncio
    async def test_retry_async_retries_a_lambda_returning_an_awaitable(self):
        """Regression: tenacity picks sync vs async by inspecting the
        callable, so a plain lambda that merely *returns* an awaitable
        (e.g. ``lambda: asyncio.to_thread(fn)``) was treated as sync and
        its coroutine handed back un-awaited -- silently skipping every
        retry. Both guardrail hallucination checks and the evaluation LLM
        judge call it exactly that way.
        """
        calls = {"n": 0}

        def blocking():
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("transient")
            return "done"

        got = await retry_async(
            lambda: asyncio.to_thread(blocking),
            max_attempts=5,
            initial_delay=0.0,
            max_delay=0.0,
            jitter=0.0,
        )
        assert got == "done"
        assert calls["n"] == 3

    @pytest.mark.asyncio
    async def test_retry_async_reraises_after_budget(self):
        async def op():
            raise OSError("always")

        with pytest.raises(OSError, match="always"):
            await retry_async(
                op, max_attempts=2, initial_delay=0.0, max_delay=0.0, jitter=0.0
            )

    @pytest.mark.asyncio
    async def test_retry_async_ignores_unlisted_exceptions(self):
        calls = {"n": 0}

        async def op():
            calls["n"] += 1
            raise ValueError("not retryable here")

        with pytest.raises(ValueError):
            await retry_async(
                op,
                max_attempts=5,
                initial_delay=0.0,
                max_delay=0.0,
                jitter=0.0,
                retry_exceptions=(OSError,),
            )
        assert calls["n"] == 1

    def test_retry_sync_recovers(self):
        calls = {"n": 0}

        def op():
            calls["n"] += 1
            if calls["n"] < 2:
                raise OSError("transient")
            return "sync-ok"

        assert (
            retry_sync(op, max_attempts=3, initial_delay=0.0, max_delay=0.0, jitter=0.0)
            == "sync-ok"
        )
        assert calls["n"] == 2

    def test_retry_sync_reraises_after_budget(self):
        def op():
            raise OSError("always")

        with pytest.raises(OSError, match="always"):
            retry_sync(op, max_attempts=2, initial_delay=0.0, max_delay=0.0, jitter=0.0)

    def test_build_sync_retrying_honours_injected_sleep(self):
        slept: list[float] = []
        calls = {"n": 0}

        def op():
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("transient")
            return "ok"

        retrying = build_sync_retrying(
            max_attempts=4,
            wait=wait_backoff(initial_delay=1.0, max_delay=60.0, jitter=0.0),
            should_retry=lambda exc, n: isinstance(exc, OSError),
            sleep=slept.append,
        )
        assert retrying(op) == "ok"
        assert slept == [1.0, 2.0]
