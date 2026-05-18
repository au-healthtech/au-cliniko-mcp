"""LLM-friendly error shaping for Cliniko API responses.

Every error returned to the LLM should:
1. Name the error class (`error`)
2. Explain what happened in plain English (`what_happened`)
3. Tell the LLM what to do next (`what_to_do`)
4. Include a working example if the fix is "call the tool differently" (`working_example`)
5. Include retry timing if the fix is "wait and retry" (`retry_after_seconds`)

This is much more useful to the model than raw `{"error": "404 Not Found"}`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMError:
    """A structured error the LLM can act on."""

    error: str
    what_happened: str
    what_to_do: str
    working_example: dict[str, Any] | None = None
    retry_after_seconds: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": self.error,
            "what_happened": self.what_happened,
            "what_to_do": self.what_to_do,
        }
        if self.working_example is not None:
            payload["working_example"] = self.working_example
        if self.retry_after_seconds is not None:
            payload["retry_after_seconds"] = self.retry_after_seconds
        payload.update(self.extra)
        return payload


def rate_limited(retry_after_seconds: int = 60) -> LLMError:
    return LLMError(
        error="rate_limit_exceeded",
        what_happened=(
            f"Cliniko returned 429 Too Many Requests. The server has already retried with "
            f"exponential backoff and is still being throttled."
        ),
        what_to_do=(
            f"Wait at least {retry_after_seconds} seconds before issuing more Cliniko calls, "
            "or reduce the volume of the query (smaller page sizes, narrower date filters)."
        ),
        retry_after_seconds=retry_after_seconds,
    )


def unauthorized() -> LLMError:
    return LLMError(
        error="unauthorized",
        what_happened="Cliniko rejected the API key (401 Unauthorized).",
        what_to_do=(
            "The API key is missing, malformed, or revoked. Ask the practitioner to "
            "regenerate it in Cliniko: My Info → Manage API keys."
        ),
    )


def not_found(resource: str, resource_id: str | int) -> LLMError:
    return LLMError(
        error="not_found",
        what_happened=f"Cliniko has no {resource} with id `{resource_id}` (404).",
        what_to_do=(
            f"Check the ID is correct. If you don't have one, list {resource}s first "
            f"and pick from the result. Cliniko IDs are 19-digit strings — do not truncate."
        ),
    )


def validation_failed(message: str, working_example: dict[str, Any] | None = None) -> LLMError:
    return LLMError(
        error="validation_failed",
        what_happened=f"The arguments failed validation: {message}",
        what_to_do=(
            "Re-call the tool with corrected arguments. See `working_example` for the "
            "expected shape."
        ),
        working_example=working_example,
    )


def upstream_unavailable(status_code: int) -> LLMError:
    return LLMError(
        error="upstream_unavailable",
        what_happened=f"Cliniko returned {status_code}. The upstream service is unhealthy.",
        what_to_do=(
            "This is a Cliniko-side problem, not a client problem. Try again in a few minutes. "
            "Check https://status.cliniko.com for incident updates."
        ),
        extra={"upstream_status": status_code},
    )
