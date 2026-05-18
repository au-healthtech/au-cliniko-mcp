"""Practice insights / KPI tools — Phase D-Insight.

Niche positioning continuation: "Heidi writes notes. We run the other 90%."

These tools answer the I-series test questions:
  - Give me my Monday morning practice summary
  - How does this month compare to last month
  - Per-practitioner scorecard
  - Patient retention by 30/60/90/180-day buckets
  - Etc.

Design:
  - Each KPI is its own tool (atomic, composable)
  - `generate_practice_digest` composes a digest using saved KPI preferences
  - Preferences are stored in the encrypted Vault per tenant
  - Default preferences = all KPIs enabled
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.phi import (
    PHI_APPOINTMENT_METADATA,
    PHI_BILLING,
    PHI_CONTACT,
    PHI_DEMOGRAPHICS,
    PHI_PATIENT_LINK,
    phi_flagged,
)
from au_cliniko_mcp.vault import Vault

# Single-tenant default for v1 — Phase F will make this per-tenant.
KPI_PREFS_VAULT_KEY = "kpi_preferences"

# Default preferences if none configured.
DEFAULT_PREFS = {
    "kpis_enabled": [
        "revenue_summary",
        "new_patients",
        "no_shows",
        "capture_rate",
        "practitioner_utilisation",
        "retention_rate",
    ],
    "default_period_days": 7,
    "comparison_period_days": 7,  # "this week vs last week"
    "retention_window_days": 180,
}

ALL_KPI_NAMES = frozenset({
    "revenue_summary",
    "new_patients",
    "no_shows",
    "capture_rate",
    "practitioner_utilisation",
    "retention_rate",
})


def _vault() -> Vault:
    """Lazy singleton — phase C vault."""
    if not hasattr(_vault, "_v"):
        _vault._v = Vault()  # type: ignore[attr-defined]
    return _vault._v  # type: ignore[attr-defined]


def _load_prefs() -> dict[str, Any]:
    raw = _vault().get(KPI_PREFS_VAULT_KEY)
    if not raw:
        return dict(DEFAULT_PREFS)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return dict(DEFAULT_PREFS)


def _save_prefs(prefs: dict[str, Any]) -> None:
    _vault().put(KPI_PREFS_VAULT_KEY, json.dumps(prefs))


def _date_range(period_days: int, *, end: date | None = None) -> tuple[date, date]:
    """Return (start, end) inclusive for the most recent N days ending today."""
    end_d = end or date.today()
    start_d = end_d - timedelta(days=period_days - 1)
    return start_d, end_d


async def _list_all_paginated(
    client: ClinikoClient,
    path: str,
    *,
    extra_params: list[tuple[str, str]] | None = None,
    response_key: str,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    """Walk pages 1..N and concatenate the result."""
    items: list[dict[str, Any]] = []
    page = 1
    params = list(extra_params or [])
    while page <= max_pages:
        r = await client.get(
            path,
            params=[("per_page", "100"), ("page", str(page)), *params],
        )
        if "error" in r:
            return items
        items.extend(r.get(response_key, []))
        if not r.get("links", {}).get("next"):
            break
        page += 1
    return items


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    """Wire the D-Insight tools onto the MCP server."""

    # ===== Preferences =====

    @mcp.tool()
    async def get_kpi_preferences() -> dict[str, Any]:
        """Show the currently-configured KPI digest preferences.

        When to use:
            - "What KPIs am I tracking in my weekly digest?"
            - As context before changing preferences

        WORKING_EXAMPLE:
            ```
            get_kpi_preferences()
            ```

        Notes:
            - Stored in the encrypted Vault. Per-tenant.
            - Default preferences (if none set): all 6 KPIs enabled with
              a 7-day default period.
        """
        prefs = _load_prefs()
        return {
            "preferences": prefs,
            "available_kpis": sorted(ALL_KPI_NAMES),
            "summary_markdown": (
                "## Current KPI preferences\n\n"
                + "\n".join(
                    f"- ✅ `{k}`" if k in prefs.get("kpis_enabled", []) else f"- ⬜ `{k}` (disabled)"
                    for k in sorted(ALL_KPI_NAMES)
                )
                + f"\n\nDefault period: **{prefs.get('default_period_days', 7)} days**"
                + f"\nComparison window: **{prefs.get('comparison_period_days', 7)} days**"
                + f"\nRetention measurement: **{prefs.get('retention_window_days', 180)} days**"
            ),
        }

    @mcp.tool()
    async def set_kpi_preferences(
        kpis_enabled: list[str] | None = None,
        default_period_days: int | None = None,
        comparison_period_days: int | None = None,
        retention_window_days: int | None = None,
    ) -> dict[str, Any]:
        """Update which KPIs the digest includes + window parameters.

        When to use:
            - User says "remove no-shows from my digest"
            - User says "I only care about revenue and new patients"
            - User says "make my comparison period a month not a week"

        WORKING_EXAMPLE:
            ```
            set_kpi_preferences(kpis_enabled=["revenue_summary", "new_patients", "capture_rate"])
            set_kpi_preferences(default_period_days=30, comparison_period_days=30)
            ```

        Notes:
            - Only fields you pass are updated; others stay as configured.
            - kpis_enabled must be a subset of: revenue_summary, new_patients,
              no_shows, capture_rate, practitioner_utilisation, retention_rate
            - Stored in the encrypted Vault.

        Args:
            kpis_enabled: which KPI tools to include in the digest.
            default_period_days: default window for "this week / month" measurements.
            comparison_period_days: window for prior-period comparison.
            retention_window_days: how far back to look for retention cohort.
        """
        prefs = _load_prefs()

        if kpis_enabled is not None:
            invalid = [k for k in kpis_enabled if k not in ALL_KPI_NAMES]
            if invalid:
                return {
                    "error": "validation_failed",
                    "what_happened": f"Unknown KPI names: {invalid}",
                    "what_to_do": f"kpis_enabled must be a subset of: {sorted(ALL_KPI_NAMES)}",
                }
            prefs["kpis_enabled"] = list(kpis_enabled)

        if default_period_days is not None:
            prefs["default_period_days"] = int(default_period_days)
        if comparison_period_days is not None:
            prefs["comparison_period_days"] = int(comparison_period_days)
        if retention_window_days is not None:
            prefs["retention_window_days"] = int(retention_window_days)

        _save_prefs(prefs)
        return {
            "saved": True,
            "preferences": prefs,
            "message": f"KPI preferences updated. {len(prefs.get('kpis_enabled', []))} KPIs enabled.",
        }

    # ===== Individual KPIs =====

    @mcp.tool()
    @phi_flagged(PHI_BILLING)
    async def kpi_revenue_summary(period_days: int = 7) -> dict[str, Any]:
        """Revenue billed in the last N days + comparison to the prior N-day window.

        When to use:
            - "How much did we bill this week?"
            - "What's our revenue trend?"

        WORKING_EXAMPLE:
            ```
            kpi_revenue_summary()              # last 7 days
            kpi_revenue_summary(period_days=30)
            ```
        """
        end = date.today()
        cur_start = end - timedelta(days=period_days - 1)
        prev_end = cur_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=period_days - 1)

        cur_inv = await _list_all_paginated(
            client, "/invoices", response_key="invoices",
            extra_params=[
                ("q[]", f"issue_date:>={cur_start.isoformat()}"),
                ("q[]", f"issue_date:<={end.isoformat()}"),
            ],
        )
        prev_inv = await _list_all_paginated(
            client, "/invoices", response_key="invoices",
            extra_params=[
                ("q[]", f"issue_date:>={prev_start.isoformat()}"),
                ("q[]", f"issue_date:<={prev_end.isoformat()}"),
            ],
        )

        def _sum(invoices: list[dict[str, Any]]) -> float:
            t = 0.0
            for i in invoices:
                try:
                    t += float(i.get("total_amount") or 0)
                except (TypeError, ValueError):
                    pass
            return t

        cur_total = _sum(cur_inv)
        prev_total = _sum(prev_inv)
        delta_pct = round(((cur_total - prev_total) / prev_total) * 100, 1) if prev_total else None

        return {
            "kpi": "revenue_summary",
            "period_days": period_days,
            "current_period": {
                "from": cur_start.isoformat(),
                "to": end.isoformat(),
                "invoices": len(cur_inv),
                "total_revenue": round(cur_total, 2),
            },
            "previous_period": {
                "from": prev_start.isoformat(),
                "to": prev_end.isoformat(),
                "invoices": len(prev_inv),
                "total_revenue": round(prev_total, 2),
            },
            "delta_pct": delta_pct,
            "summary_markdown": (
                f"**Revenue (last {period_days}d)**: ${cur_total:,.2f} from {len(cur_inv)} invoices  "
                + (f"({'+' if (delta_pct or 0) >= 0 else ''}{delta_pct}% vs prior {period_days}d ${prev_total:,.2f})"
                   if delta_pct is not None else "")
            ),
        }

    @mcp.tool()
    @phi_flagged(PHI_DEMOGRAPHICS, PHI_CONTACT)
    async def kpi_new_patients(period_days: int = 7) -> dict[str, Any]:
        """New patients added in the last N days + comparison to the prior N-day window."""
        end = date.today()
        cur_start = end - timedelta(days=period_days - 1)
        prev_end = cur_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=period_days - 1)

        # Cliniko's patient.created_at field is queryable via q[]
        cur = await _list_all_paginated(
            client, "/patients", response_key="patients",
            extra_params=[
                ("q[]", f"created_at:>={cur_start.isoformat()}T00:00:00Z"),
                ("q[]", f"created_at:<={end.isoformat()}T23:59:59Z"),
            ],
        )
        prev = await _list_all_paginated(
            client, "/patients", response_key="patients",
            extra_params=[
                ("q[]", f"created_at:>={prev_start.isoformat()}T00:00:00Z"),
                ("q[]", f"created_at:<={prev_end.isoformat()}T23:59:59Z"),
            ],
        )

        delta = len(cur) - len(prev)
        return {
            "kpi": "new_patients",
            "period_days": period_days,
            "current_period_count": len(cur),
            "previous_period_count": len(prev),
            "delta": delta,
            "sample_recent_patients": [
                {"id": p["id"], "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(), "created_at": p.get("created_at")}
                for p in cur[:5]
            ],
            "summary_markdown": (
                f"**New patients (last {period_days}d)**: {len(cur)} "
                f"({'+' if delta >= 0 else ''}{delta} vs prior {period_days}d)"
            ),
        }

    @mcp.tool()
    @phi_flagged(PHI_APPOINTMENT_METADATA)
    async def kpi_no_shows(period_days: int = 28) -> dict[str, Any]:
        """No-show rate + recent no-show appointments."""
        end = date.today()
        start = end - timedelta(days=period_days - 1)
        all_appts = await _list_all_paginated(
            client, "/individual_appointments", response_key="individual_appointments",
            extra_params=[
                ("q[]", f"starts_at:>={start.isoformat()}T00:00:00Z"),
                ("q[]", f"starts_at:<={end.isoformat()}T23:59:59Z"),
            ],
        )
        total = len(all_appts)
        no_shows = [a for a in all_appts if a.get("did_not_arrive")]
        rate = round(len(no_shows) / total, 3) if total else 0.0
        return {
            "kpi": "no_shows",
            "period_days": period_days,
            "total_appointments": total,
            "no_show_count": len(no_shows),
            "no_show_rate": rate,
            "sample_no_shows": [
                {"id": a["id"], "starts_at": a.get("starts_at")} for a in no_shows[:10]
            ],
            "summary_markdown": (
                f"**No-shows (last {period_days}d)**: {len(no_shows)}/{total} "
                f"({rate*100:.1f}% no-show rate)"
            ),
        }

    @mcp.tool()
    @phi_flagged(PHI_BILLING, PHI_APPOINTMENT_METADATA)
    async def kpi_capture_rate(period_days: int = 7) -> dict[str, Any]:
        """% of appointments invoiced — direct revenue-leakage signal."""
        end = date.today()
        start = end - timedelta(days=period_days - 1)

        appts = await _list_all_paginated(
            client, "/individual_appointments", response_key="individual_appointments",
            extra_params=[
                ("q[]", f"starts_at:>={start.isoformat()}T00:00:00Z"),
                ("q[]", f"starts_at:<={end.isoformat()}T23:59:59Z"),
            ],
        )
        inv_from = (start - timedelta(days=7)).isoformat()
        inv_to = (end + timedelta(days=14)).isoformat()
        invoices = await _list_all_paginated(
            client, "/invoices", response_key="invoices",
            extra_params=[
                ("q[]", f"issue_date:>={inv_from}"),
                ("q[]", f"issue_date:<={inv_to}"),
            ],
        )
        appt_with_inv = set()
        for inv in invoices:
            link = (inv.get("appointment") or {}).get("links", {}).get("self", "")
            if link:
                appt_with_inv.add(link.rstrip("/").rsplit("/", 1)[-1])

        billed = sum(1 for a in appts if a["id"] in appt_with_inv)
        rate = round(billed / len(appts), 3) if appts else 0.0

        return {
            "kpi": "capture_rate",
            "period_days": period_days,
            "total_appointments": len(appts),
            "appointments_invoiced": billed,
            "capture_rate": rate,
            "summary_markdown": (
                f"**Capture rate (last {period_days}d)**: {billed}/{len(appts)} "
                f"appointments invoiced ({rate*100:.1f}%)"
            ),
        }

    @mcp.tool()
    @phi_flagged(PHI_APPOINTMENT_METADATA)
    async def kpi_practitioner_utilisation(period_days: int = 7) -> dict[str, Any]:
        """Per-practitioner booked-appointment counts over the period."""
        end = date.today()
        start = end - timedelta(days=period_days - 1)

        pr = await client.get("/practitioners", params={"per_page": 100})
        practs = [p for p in pr.get("practitioners", []) if p.get("active")]
        rows = []
        for p in practs:
            ar = await client.get("/individual_appointments", params=[
                ("per_page", "1"),
                ("q[]", f"practitioner_id:={p['id']}"),
                ("q[]", f"starts_at:>={start.isoformat()}T00:00:00Z"),
                ("q[]", f"starts_at:<={end.isoformat()}T23:59:59Z"),
            ])
            n = ar.get("total_entries", 0) if "error" not in ar else 0
            rows.append({
                "id": p["id"],
                "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip() or "(no name)",
                "booked_appointments": n,
            })
        rows.sort(key=lambda x: x["booked_appointments"], reverse=True)
        for rank, r in enumerate(rows, 1):
            r["rank"] = rank
        md_lines = ["**Practitioner utilisation:**"]
        for r in rows:
            md_lines.append(f"  {r['rank']}. {r['name']} — {r['booked_appointments']} appts")
        return {
            "kpi": "practitioner_utilisation",
            "period_days": period_days,
            "practitioners": rows,
            "summary_markdown": "\n".join(md_lines),
        }

    @mcp.tool()
    @phi_flagged(PHI_DEMOGRAPHICS, PHI_APPOINTMENT_METADATA)
    async def kpi_retention_rate(window_days: int = 180) -> dict[str, Any]:
        """% of new patients (from window_days ago) who returned for a 2nd appointment.

        Cohort: patients whose first appointment was in [window_days .. window_days+30] ago.
        We look at how many had a SECOND appointment subsequently.
        """
        end = date.today()
        cohort_end = end - timedelta(days=window_days)
        cohort_start = cohort_end - timedelta(days=30)

        cohort = await _list_all_paginated(
            client, "/patients", response_key="patients",
            extra_params=[
                ("q[]", f"created_at:>={cohort_start.isoformat()}T00:00:00Z"),
                ("q[]", f"created_at:<={cohort_end.isoformat()}T23:59:59Z"),
            ],
        )
        if not cohort:
            return {
                "kpi": "retention_rate",
                "window_days": window_days,
                "cohort_size": 0,
                "summary_markdown": (
                    f"**Retention rate** (cohort: patients added ~{window_days}d ago, ±30d): "
                    "no patients in this cohort window."
                ),
            }

        returned = 0
        for p in cohort[:50]:  # cap N+1 cost at 50 patients
            r = await client.get(
                "/individual_appointments",
                params=[("per_page", "2"), ("q[]", f"patient_id:={p['id']}")],
            )
            n = r.get("total_entries", 0) if "error" not in r else 0
            if n >= 2:
                returned += 1
        rate = round(returned / min(len(cohort), 50), 3)
        return {
            "kpi": "retention_rate",
            "window_days": window_days,
            "cohort_size": len(cohort),
            "cohort_sampled": min(len(cohort), 50),
            "returned_for_second": returned,
            "retention_rate": rate,
            "summary_markdown": (
                f"**Retention rate** (cohort of {min(len(cohort), 50)} patients added "
                f"~{window_days}d ago): {returned} returned for a 2nd appt ({rate*100:.1f}%)"
            ),
        }

    # ===== Digest composer =====

    @mcp.tool()
    @phi_flagged(PHI_BILLING, PHI_APPOINTMENT_METADATA, PHI_DEMOGRAPHICS)
    async def generate_practice_digest() -> dict[str, Any]:
        """Compose a practice-performance digest using saved KPI preferences.

        When to use:
            - "Give me my Monday morning practice summary"
            - "What's my weekly digest?"
            - Anytime the user wants a one-shot performance snapshot

        WORKING_EXAMPLE:
            ```
            generate_practice_digest()
            ```

        Notes:
            - Uses preferences saved via set_kpi_preferences (or DEFAULT_PREFS).
            - For Phase D-Workflow integration: this digest text can be emailed
              via the Gmail MCP, but the digest tool itself does not send.
        """
        prefs = _load_prefs()
        enabled = prefs.get("kpis_enabled", list(DEFAULT_PREFS["kpis_enabled"]))
        period = prefs.get("default_period_days", 7)
        retention_window = prefs.get("retention_window_days", 180)

        sections: list[str] = [f"# Practice Digest — {date.today().isoformat()}", ""]
        results: dict[str, Any] = {}

        # Map name → coroutine
        async def run(name: str) -> dict[str, Any]:
            if name == "revenue_summary":
                return await kpi_revenue_summary(period_days=period)
            if name == "new_patients":
                return await kpi_new_patients(period_days=period)
            if name == "no_shows":
                return await kpi_no_shows(period_days=max(period, 28))
            if name == "capture_rate":
                return await kpi_capture_rate(period_days=period)
            if name == "practitioner_utilisation":
                return await kpi_practitioner_utilisation(period_days=period)
            if name == "retention_rate":
                return await kpi_retention_rate(window_days=retention_window)
            return {"error": "unknown_kpi", "name": name}

        for kpi_name in enabled:
            if kpi_name not in ALL_KPI_NAMES:
                continue
            result = await run(kpi_name)
            results[kpi_name] = result
            md = result.get("summary_markdown")
            if md:
                sections.append(md)
                sections.append("")

        digest_md = "\n".join(sections)
        return {
            "digest_markdown": digest_md,
            "preferences_used": prefs,
            "kpis_run": list(results.keys()),
            "kpi_data": results,
        }
