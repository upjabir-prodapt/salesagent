"""Who a given LLM call is being made on behalf of.

The Apigee `llm` gateway meters and rate-limits on two asserted headers,
`x-colt-user-oid` and `x-colt-user-department` (AICOE-Terraform `docs/23` S4.2).
Every LLM call must carry them or it lands in the gateway's
`system`/`unattributed` bucket, and neither per-user nor per-department quotas
mean anything.

Why a ContextVar
----------------
Identity enters the worker once, in `ResearchJobRunner.run`, and is needed at
call sites that have no job object in scope at all -- `utils/guardrails.py` and
`evaluation/service.py` among them. Threading it explicitly would mean changing
`AdkAgentStep.build_agent()` (which takes no arguments), all four steps,
`execute`, and every agent test -- and would still miss those two. A ContextVar
reaches all of them.

`asyncio.to_thread` copies the current context, as does task creation and
`asyncio.gather`, so every offload in this service sees the identity.

**Where this must be read matters.** This service's genai client is a
process-wide singleton and `ApigeeLlm.api_client` is a `cached_property`, so
identity read at *client construction* would freeze the first job's user onto
every later call for the container's lifetime -- silently mis-attributing
everything with no error. Identity is therefore applied **per request**, via
`GenerateContentConfig.http_options`, never baked into a client.

Trust model
-----------
These values are ASSERTED by this backend, not derived from a verified token --
at worker time the user's Entra token is long gone. That is the same
hybrid-trust compromise already accepted for `department` on the `int` proxy,
and it is acceptable because callers are trusted internal services gated by API
key and network isolation. It does mean these quotas are a **cost-control
mechanism, not a security boundary** -- do not present them as the latter.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass

__all__ = ["LlmIdentity", "current_llm_identity", "use_llm_identity"]


@dataclass(frozen=True, slots=True)
class LlmIdentity:
    """The user an LLM call is attributed to."""

    oid: str = ""
    department: str = ""


_current: ContextVar[LlmIdentity | None] = ContextVar("llm_identity", default=None)


def current_llm_identity() -> LlmIdentity | None:
    """The identity in scope, or None when there is no user behind this call."""
    return _current.get()


@contextlib.contextmanager
def use_llm_identity(oid: str | None, department: str | None) -> Iterator[None]:
    """Attribute every LLM call made inside this block to one user.

    Blank values are kept blank rather than replaced with invented sentinels:
    the gateway helper omits the corresponding header and Apigee's own
    `AM-Identity` policy applies its documented `system` / `unattributed`
    defaults. Deciding that here as well would give two places to disagree
    about what "no user" means.

    The token is always reset, so a later job on the same Cloud Run instance
    cannot inherit the previous job's user -- these containers are reused, and
    concurrent jobs each get their own context.
    """
    token = _current.set(
        LlmIdentity(oid=(oid or "").strip(), department=(department or "").strip())
    )
    try:
        yield
    finally:
        _current.reset(token)
