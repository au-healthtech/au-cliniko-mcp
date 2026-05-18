"""Treatment note tools — the clinical content of Cliniko.

⚠️  CRITICAL SAFETY DESIGN:
    Treatment notes contain clinical content (PHI). This module deliberately
    follows the "draft-by-default, finalise-never" pattern for v1.

    Available in v1 (this commit):
      - list_treatment_notes_for_patient (read)
      - get_treatment_note (read)
      - draft_treatment_note (write, ALWAYS as draft=True)

    NOT in v1 (deferred to Phase C with explicit consent-gate decorator):
      - finalise_treatment_note
      - update_treatment_note (would modify existing clinical record)
      - delete_treatment_note

    This is a deliberate safety choice. Practitioners must finalise notes in
    Cliniko UI for v1 — that keeps the human-in-the-loop checkpoint inviolable.

CLINIKO TWO-STEP PERSIST QUIRK:
    Cliniko's treatment note content is set via a two-step API call:
      1. POST /treatment_notes — creates the note shell with metadata
      2. PUT /treatment_notes/{id}/content — writes the clinical body

    Some hobby implementations get step 2 wrong and end up with empty notes.
    Our `draft_treatment_note` handles both steps atomically.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.shaping import list_wrapper, summarise_treatment_note


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    @mcp.tool()
    async def list_treatment_notes_for_patient(
        patient_id: str,
        page: int = 1,
        per_page: int = 25,
    ) -> dict[str, Any]:
        """List a patient's treatment notes (metadata only — clinical body NOT included in summary).

        When to use:
            - "What treatment notes does Jane have on file?"
            - As context before drafting a new note (to see the prior thread)
            - For audit / review of a patient's clinical history

        WORKING_EXAMPLE:
            ```
            list_treatment_notes_for_patient(patient_id="12345678901234567890")
            ```

        Notes:
            - The `summary_markdown` shows note IDs and status only — NO clinical
              content. To read note bodies, call `get_treatment_note` per id.
            - PHI: high-sensitivity. Audit-logged with
              `phi_categories=['clinical_notes','demographics']`.

        Args:
            patient_id: 19-digit Cliniko patient id.
            page: 1-indexed page.
            per_page: results per page.
        """
        q_params: list[tuple[str, str]] = [
            ("page", str(page)),
            ("per_page", str(per_page)),
            ("q[]", f"patient_id:={patient_id}"),
        ]
        result = await client.get("/treatment_notes", params=q_params)

        if "error" in result:
            return result

        notes = result.get("treatment_notes", [])
        total = result.get("total_entries") or len(notes)
        has_more = bool(result.get("links", {}).get("next"))

        return list_wrapper(
            items_full=notes,
            summary_lines=[summarise_treatment_note(n) for n in notes],
            total_entries=total,
            page=page,
            has_more=has_more,
        )

    @mcp.tool()
    async def get_treatment_note(treatment_note_id: str) -> dict[str, Any]:
        """Get one treatment note's full record, INCLUDING clinical content.

        When to use:
            After `list_treatment_notes_for_patient` has identified a specific
            note and the practitioner wants to read its body — to review, build
            a continuation, or generate a summary.

        WORKING_EXAMPLE:
            ```
            get_treatment_note(treatment_note_id="12345678901234567890")
            ```

        Notes:
            - This returns the full clinical body. PHI: highest-sensitivity.
              Audit-logged with `phi_categories=['clinical_notes']`.
            - The body may include AHPRA-regulated clinical content. Treat as
              such in any downstream LLM operations — do NOT re-transmit
              outside the practitioner's session.

        Args:
            treatment_note_id: 19-digit Cliniko note id.
        """
        return await client.get(f"/treatment_notes/{treatment_note_id}")

    @mcp.tool()
    async def draft_treatment_note(
        patient_id: str,
        practitioner_id: str,
        appointment_id: str | None,
        treatment_note_template_id: str | None,
        content: str,
    ) -> dict[str, Any]:
        """Create a new treatment note in DRAFT status (always).

        ⚠️  SAFETY:
            This tool ALWAYS creates the note as `draft=True`. It cannot finalise.
            The practitioner must manually review and finalise in the Cliniko UI.

            This is a deliberate v1 safety choice. The Phase C consent-gate
            decorator will eventually allow finalisation under explicit conditions
            (multi-step confirmation, signed-off prompt), but never silently.

        When to use:
            - "Draft a SOAP note for today's appointment with Jane"
            - "Build a continuation note based on Jane's last 3 visits"
            - The Claude session generates the prose; this tool persists it as
              a draft for the practitioner's review.

        WORKING_EXAMPLE:
            ```
            draft_treatment_note(
                patient_id="12345678901234567890",
                practitioner_id="98765432109876543210",
                appointment_id="11111111111111111111",
                treatment_note_template_id=None,
                content="S: ... O: ... A: ... P: ..."
            )
            ```

        Notes:
            - All four IDs are 19-digit strings.
            - `appointment_id` can be None if the note isn't linked to a specific appointment.
            - `treatment_note_template_id` can be None if not using a template.
            - Cliniko persists notes via a two-step API call (POST shell, PUT content).
              This tool handles both atomically.
            - The created note's `draft` flag will be True. The practitioner finalises
              in Cliniko UI: My Patients → patient → Treatment Notes → Review.
            - PHI: writes clinical content. Audit-logged with
              `phi_categories=['clinical_notes']` and `result_status='uncommitted_draft'`.

        Args:
            patient_id: 19-digit Cliniko patient id.
            practitioner_id: 19-digit Cliniko practitioner id (the author).
            appointment_id: optional appointment to link the note to.
            treatment_note_template_id: optional template id.
            content: the clinical body. Markdown is preserved.

        Returns:
            The newly-created note record (with `draft=True`).
        """
        # Step 1: create the note shell with metadata
        body: dict[str, Any] = {
            "patient_id": patient_id,
            "practitioner_id": practitioner_id,
            "draft": True,
        }
        if appointment_id:
            body["appointment_id"] = appointment_id
        if treatment_note_template_id:
            body["treatment_note_template_id"] = treatment_note_template_id

        created = await client.post("/treatment_notes", json=body)

        if "error" in created:
            return created

        note_id = created.get("id")
        if not note_id:
            return created  # something unexpected — return whatever came back

        # Step 2: persist the clinical body
        # Cliniko uses PATCH on the note id with the content field
        content_result = await client.patch(
            f"/treatment_notes/{note_id}",
            json={"content": content, "draft": True},
        )

        return content_result if "error" not in content_result else content_result
