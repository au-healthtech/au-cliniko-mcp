"""Unit tests for shaping.py — the markdown summary builders.

These tests exercise the helpers with fixture data (no network). They guard
against the kinds of "lost ID precision" / "wrong link parsing" bugs the
hobby implementations had.
"""

from __future__ import annotations

from au_cliniko_mcp.shaping import (
    _id_from_link,
    list_wrapper,
    summarise_appointment,
    summarise_business,
    summarise_invoice,
    summarise_patient,
    summarise_practitioner,
    summarise_recall,
    summarise_treatment_note,
)


class TestIdFromLink:
    def test_extracts_trailing_id(self):
        assert (
            _id_from_link("https://api.au1.cliniko.com/v1/patients/12345678901234567890")
            == "12345678901234567890"
        )

    def test_handles_trailing_slash(self):
        assert _id_from_link("https://api.au1.cliniko.com/v1/patients/123/") == "123"

    def test_empty_input_returns_question_mark(self):
        assert _id_from_link("") == "?"

    def test_no_slash_returns_original(self):
        assert _id_from_link("12345") == "12345"

    def test_preserves_full_19_digit_string(self):
        # Critical: Python ints can hold 19 digits but JSON round-trips lose precision
        # in some clients. We must keep IDs as strings end-to-end.
        link = "https://api.au1.cliniko.com/v1/patients/1234567890123456789"
        result = _id_from_link(link)
        assert result == "1234567890123456789"
        assert isinstance(result, str)
        assert len(result) == 19


class TestListWrapper:
    def test_basic_shape(self):
        result = list_wrapper(
            items_full=[{"id": "1"}, {"id": "2"}],
            summary_lines=["- one", "- two"],
        )
        assert result["items"] == [{"id": "1"}, {"id": "2"}]
        assert result["total_entries"] == 2
        assert result["page"] == 1
        assert result["has_more"] is False
        assert result["next_cursor"] is None
        assert result["summary_markdown"] == "- one\n- two"

    def test_empty_list(self):
        result = list_wrapper(items_full=[], summary_lines=[])
        assert result["items"] == []
        assert result["total_entries"] == 0
        assert result["summary_markdown"] == "_(no items)_"

    def test_total_entries_override(self):
        # When upstream reports more entries than the current page has
        result = list_wrapper(
            items_full=[{"id": "1"}],
            summary_lines=["- one"],
            total_entries=1000,
            page=1,
            has_more=True,
        )
        assert result["total_entries"] == 1000
        assert result["has_more"] is True


class TestSummarisePatient:
    def test_complete_record(self):
        s = summarise_patient(
            {
                "id": "12345",
                "first_name": "Jane",
                "last_name": "Smith",
                "date_of_birth": "1990-01-01",
                "email": "j@s.com",
                "patient_phone_numbers": [{"number": "0400 000 000"}],
            }
        )
        assert "Jane Smith" in s
        assert "12345" in s
        assert "1990-01-01" in s
        assert "j@s.com" in s
        assert "0400 000 000" in s

    def test_missing_phone_falls_back(self):
        s = summarise_patient(
            {"id": "1", "first_name": "X", "last_name": "Y", "email": "x@y.com"}
        )
        assert "—" in s  # phone fallback

    def test_handles_missing_name(self):
        s = summarise_patient({"id": "1"})
        assert "(no name)" in s


class TestSummariseAppointment:
    def test_extracts_ids_from_hateoas_links(self):
        s = summarise_appointment(
            {
                "id": "999",
                "starts_at": "2026-05-19T09:00",
                "ends_at": "2026-05-19T10:00",
                "patient": {"links": {"self": "https://api.au1.cliniko.com/v1/patients/111"}},
                "practitioner": {
                    "links": {"self": "https://api.au1.cliniko.com/v1/practitioners/222"}
                },
            }
        )
        assert "999" in s
        assert "patient `111`" in s
        assert "practitioner `222`" in s

    def test_marks_cancelled(self):
        s = summarise_appointment(
            {
                "id": "999",
                "starts_at": "x",
                "ends_at": "y",
                "patient": {"links": {"self": "/p/1"}},
                "practitioner": {"links": {"self": "/p/2"}},
                "cancelled_at": "2026-05-18T08:00",
            }
        )
        assert "CANCELLED" in s


class TestSummariseTreatmentNote:
    def test_does_not_leak_clinical_content(self):
        """Treatment-note summary must never include the note body — PHI."""
        s = summarise_treatment_note(
            {
                "id": "777",
                "created_at": "2026-05-18",
                "draft": False,
                "content": "S: patient reports severe pain in left foot. O: ...",
                "practitioner": {"links": {"self": "/practitioners/2"}},
            }
        )
        assert "777" in s
        assert "finalised" in s
        assert "severe pain" not in s  # must not leak content
        assert "patient reports" not in s

    def test_marks_draft_status(self):
        s = summarise_treatment_note(
            {"id": "1", "draft": True, "created_at": "x", "practitioner": {"links": {"self": "/p/1"}}}
        )
        assert "📝 draft" in s


class TestSummariseInvoice:
    def test_includes_balance_and_status(self):
        s = summarise_invoice(
            {
                "id": "444",
                "number": "INV-001",
                "total": "150.00",
                "balance": "50.00",
                "status": "awaiting_payment",
                "issue_date": "2026-05-01",
            }
        )
        assert "INV-001" in s
        assert "$150.00" in s
        assert "$50.00" in s
        assert "awaiting_payment" in s
        assert "2026-05-01" in s


class TestSummarisePractitioner:
    def test_basic_record(self):
        s = summarise_practitioner(
            {
                "id": "222",
                "title": "Mr",
                "first_name": "Tradd",
                "last_name": "Horne",
                "designation": "Podiatrist",
                "active": True,
            }
        )
        assert "Tradd Horne" in s
        assert "222" in s
        assert "Podiatrist" in s
        assert "active" in s

    def test_inactive_practitioner(self):
        s = summarise_practitioner({"id": "1", "first_name": "X", "active": False})
        assert "inactive" in s


class TestSummariseBusiness:
    def test_basic_record(self):
        s = summarise_business(
            {
                "id": "333",
                "business_name": "Principal Podiatry",
                "city": "Brighton",
                "state": "QLD",
                "country": "Australia",
            }
        )
        assert "Principal Podiatry" in s
        assert "Brighton" in s
        assert "QLD" in s

    def test_missing_location_parts(self):
        s = summarise_business({"id": "1", "business_name": "X"})
        assert "X" in s


class TestSummariseRecall:
    def test_truncates_long_note(self):
        long_note = "x" * 100
        s = summarise_recall({"id": "1", "due_at": "2026-06-01", "note": long_note})
        assert "…" in s  # truncation marker
        assert "2026-06-01" in s
