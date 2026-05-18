"""Invoice tools.

Cliniko has a non-obvious API limitation worth flagging in advance: the API
does NOT support full create/update/delete of invoices — only `POST` to issue
an already-drafted invoice. Invoice creation must happen in the Cliniko UI.

This file therefore reads + lists + retrieves invoices and surfaces the
limitation clearly. `docs/API-LIMITATIONS.md` carries the full details.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.shaping import list_wrapper, summarise_invoice


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    @mcp.tool()
    async def list_invoices(
        from_date: str | None = None,
        to_date: str | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict[str, Any]:
        """List invoices, optionally filtered by issue-date range or status.

        When to use:
            - "What invoices were issued this week?"
            - "Show me unpaid invoices"
            - As input to invoice-chase / aged-debt workflows

        WORKING_EXAMPLE:
            ```
            list_invoices(from_date="2026-05-01", to_date="2026-05-31")
            list_invoices(status="unpaid")
            ```

        Notes:
            - Dates are ISO-8601 (`YYYY-MM-DD`) and filter on `issue_date`.
            - `status` can be: `draft`, `awaiting_payment`, `paid`, `void`.
              For "unpaid" use `awaiting_payment` (Cliniko's exact term).
            - PHI: invoices link to patient records. Audit-logged with
              `phi_categories=['billing','patient_link']`.

        Args:
            from_date: earliest issue date to include.
            to_date: latest issue date to include.
            status: Cliniko invoice status filter.
            page: 1-indexed page number.
            per_page: results per page.
        """
        q_params: list[tuple[str, str]] = [
            ("page", str(page)),
            ("per_page", str(per_page)),
        ]
        if from_date:
            q_params.append(("q[]", f"issue_date:>={from_date}"))
        if to_date:
            q_params.append(("q[]", f"issue_date:<={to_date}"))
        if status:
            q_params.append(("q[]", f"status:={status}"))

        result = await client.get("/invoices", params=q_params)

        if "error" in result:
            return result

        invoices = result.get("invoices", [])
        total = result.get("total_entries") or len(invoices)
        has_more = bool(result.get("links", {}).get("next"))

        return list_wrapper(
            items_full=invoices,
            summary_lines=[summarise_invoice(i) for i in invoices],
            total_entries=total,
            page=page,
            has_more=has_more,
        )

    @mcp.tool()
    async def list_unpaid_invoices(over_days: int = 30, per_page: int = 50) -> dict[str, Any]:
        """List invoices that are awaiting payment, optionally older than N days.

        When to use:
            - "Who owes us money?"
            - "List unpaid invoices over 30 days old"
            - Generating the weekly invoice-chase list

        WORKING_EXAMPLE:
            ```
            list_unpaid_invoices(over_days=30)
            list_unpaid_invoices(over_days=0)   # all unpaid regardless of age
            ```

        Notes:
            - This is shorthand for `list_invoices(status="awaiting_payment", to_date=<N days ago>)`.
            - PHI: same as `list_invoices`.

        Args:
            over_days: only include invoices issued more than this many days ago. Default 30.
            per_page: results per page.
        """
        from datetime import date, timedelta

        cutoff = (date.today() - timedelta(days=over_days)).isoformat()

        q_params: list[tuple[str, str]] = [
            ("per_page", str(per_page)),
            ("q[]", "status:=awaiting_payment"),
        ]
        if over_days > 0:
            q_params.append(("q[]", f"issue_date:<={cutoff}"))

        result = await client.get("/invoices", params=q_params)

        if "error" in result:
            return result

        invoices = result.get("invoices", [])
        total = result.get("total_entries") or len(invoices)

        return list_wrapper(
            items_full=invoices,
            summary_lines=[summarise_invoice(i) for i in invoices],
            total_entries=total,
        )

    @mcp.tool()
    async def get_invoice(invoice_id: str) -> dict[str, Any]:
        """Get one invoice by id, including line items and payment history.

        When to use:
            After `list_invoices` has identified a specific invoice and you
            need the full breakdown — line items, taxes, payments applied.

        WORKING_EXAMPLE:
            ```
            get_invoice(invoice_id="12345678901234567890")
            ```

        Notes:
            - Invoice IDs are 19-digit strings.
            - Cliniko API does NOT support creating, updating, or voiding
              invoices via API. Those operations require the Cliniko UI.
              See `docs/API-LIMITATIONS.md`.
            - PHI: same as `list_invoices`.
        """
        return await client.get(f"/invoices/{invoice_id}")
