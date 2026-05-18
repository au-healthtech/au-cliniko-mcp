"""PHI (Protected Health Information) guard infrastructure.

Why this exists:
    Under the Privacy Act 1988 (APP 11), an entity that handles personal/health
    information must take reasonable steps to protect it from misuse, interference,
    loss and unauthorised access. The OAIC's guidance for health-service providers
    explicitly calls out logging + access controls.

    Every MCP tool that touches PHI carries a `@phi_flagged(*categories)` decorator
    declaring the categories of PHI it returns. The decorator:
        1. Wraps the tool with timing + audit logging
        2. Tags the audit record with the declared PHI categories
        3. Extracts patient_id / practitioner_id from args when present
        4. Attaches a `_phi` header to the response so the LLM knows what it has
           and can decide whether to surface or redact in chat

PHI category taxonomy (extensible):
    - demographics      DOB, sex, country of birth, marital status, occupation
    - contact           email, phone, address, suburb, postcode
    - appointment_metadata  appointment times, types, statuses (no clinical content)
    - clinical_notes    treatment notes / SOAP content
    - medical_alerts    free-text alert content (allergies, conditions)
    - billing           invoice items, payments, balance
    - communications    SMS/email content, sender, channel
    - attachments       file metadata (we deliberately don't read content)
    - patient_link      an ID that links to a patient record (low-sensitivity, but trackable)

Use:
    @mcp.tool()
    @phi_flagged("demographics", "contact")
    async def list_patients(...):
        ...
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any, Awaitable, Callable

from au_cliniko_mcp.audit import _stopwatch_ms_since, get_audit_log

logger = logging.getLogger("au_cliniko_mcp.phi")

# Category constants — keep this list in sync with COMPLIANCE.md.
PHI_DEMOGRAPHICS = "demographics"
PHI_CONTACT = "contact"
PHI_APPOINTMENT_METADATA = "appointment_metadata"
PHI_CLINICAL_NOTES = "clinical_notes"
PHI_MEDICAL_ALERTS = "medical_alerts"
PHI_BILLING = "billing"
PHI_COMMUNICATIONS = "communications"
PHI_ATTACHMENTS = "attachments"
PHI_PATIENT_LINK = "patient_link"

ALL_PHI_CATEGORIES = frozenset({
    PHI_DEMOGRAPHICS,
    PHI_CONTACT,
    PHI_APPOINTMENT_METADATA,
    PHI_CLINICAL_NOTES,
    PHI_MEDICAL_ALERTS,
    PHI_BILLING,
    PHI_COMMUNICATIONS,
    PHI_ATTACHMENTS,
    PHI_PATIENT_LINK,
})


def phi_flagged(*categories: str, write: bool = False) -> Callable:
    """Decorator: mark a tool's PHI categories + wire audit logging.

    Args:
        *categories: PHI categories this tool returns. Use the PHI_* constants.
        write: True if the tool MUTATES Cliniko state. Affects result_status
            classification (uncommitted_draft / committed / cost_blocked).
    """
    invalid = [c for c in categories if c not in ALL_PHI_CATEGORIES]
    if invalid:
        raise ValueError(f"Unknown PHI categories: {invalid}. See phi.py for the taxonomy.")
    categories_list = list(categories)

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            t0 = time.time()
            patient_id = _extract_id(kwargs, "patient_id")
            practitioner_id = _extract_id(kwargs, "practitioner_id")
            audit = get_audit_log()

            result: Any = None
            status = "ok"
            error_msg: str | None = None
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                status = "error"
                error_msg = repr(exc)
                # Audit then re-raise — the caller still gets the error
                await audit.record(
                    tool_name=fn.__name__,
                    args=kwargs,
                    phi_categories=categories_list,
                    result_status=status,
                    elapsed_ms=_stopwatch_ms_since(t0),
                    patient_id=patient_id,
                    practitioner_id=practitioner_id,
                    error=error_msg,
                )
                raise

            # Classify result_status based on what came back
            if isinstance(result, dict):
                if result.get("needs_confirmation"):
                    status = "cost_blocked"
                elif result.get("error"):
                    status = "error"
                    error_msg = str(result.get("error"))
                elif write:
                    # A successful write — was it a draft or a real commit?
                    if result.get("draft") is True:
                        status = "uncommitted_draft"
                    else:
                        status = "committed"

            # Attach _phi header to the response so the LLM can reason about it
            if isinstance(result, dict) and categories_list:
                result["_phi"] = {
                    "categories": categories_list,
                    "guidance": (
                        "This response contains PHI ("
                        + ", ".join(categories_list)
                        + "). Treat as sensitive. Audit record written."
                    ),
                }

            await audit.record(
                tool_name=fn.__name__,
                args=kwargs,
                phi_categories=categories_list,
                result_status=status,
                elapsed_ms=_stopwatch_ms_since(t0),
                patient_id=patient_id,
                practitioner_id=practitioner_id,
                error=error_msg,
            )
            return result

        # Annotate the function so introspection can see the categories
        wrapped._phi_categories = categories_list  # type: ignore[attr-defined]
        wrapped._phi_write = write  # type: ignore[attr-defined]
        return wrapped

    return decorator


def _extract_id(kwargs: dict[str, Any], key: str) -> str | None:
    """Pull a string ID out of kwargs without raising if missing/non-string."""
    val = kwargs.get(key)
    if isinstance(val, str) and val:
        return val
    return None
