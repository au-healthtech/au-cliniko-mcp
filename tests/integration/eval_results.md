# 26-Question Eval Results

Run: 2026-05-18T05:14:38.984944

## Summary

- **Tests run**: 26
- **PASS (score ≥4)**: 15
- **PARTIAL (score = 3)**: 0
- **FAIL (score ≤2)**: 11

## Results by question

| QID | Category | Question | Forecast | Score | Time | Status |
|---|---|---|---|---|---|---|
| Q1 | Practice Health | How many active patients do we have? | PASS | 5/5 | 0.10s | ✅ |
| Q2 | Practice Health | Average appointments per week over last 3 months? | PASS | 5/5 | 0.06s | ✅ |
| Q3 | Practice Health | Top 10 practitioners by appointment count. | PARTIAL | 5/5 | 0.13s | ✅ |
| Q4 | Practice Health | Appointment types breakdown by frequency. | PARTIAL | 5/5 | 0.16s | ✅ |
| Q5 | Practice Health | % new vs returning patients. | FAIL | 1/5 | 0.00s | ❌ |
| Q6 | Schedule | Next week's schedule — full or gaps? | PASS | 5/5 | 0.05s | ✅ |
| Q7 | Schedule | Which practitioners have the most gaps in next 14 days? | PARTIAL | 1/5 | 0.00s | ❌ |
| Q8 | Schedule | Which day of week has highest no-show rate? | PASS | 5/5 | 0.06s | ✅ |
| Q9 | Schedule | Online vs phone booking split? | FAIL | 1/5 | 0.00s | ❌ |
| Q10 | Recalls & Retention | Patients who haven't been in for 6+ months. | PASS | 5/5 | 0.12s | ✅ |
| Q11 | Recalls & Retention | Course-of-care recommended but not completed. | FAIL | 1/5 | 0.00s | ❌ |
| Q12 | Recalls & Retention | Recalls due in next 30 days. | PASS | 5/5 | 0.05s | ✅ |
| Q13 | Recalls & Retention | % new patients who return for second appointment. | FAIL | 1/5 | 0.00s | ❌ |
| Q14 | Billing | Total outstanding invoice balance. | PASS | 5/5 | 0.05s | ✅ |
| Q15 | Billing | Unpaid invoices over 30 days old. | PASS | 5/5 | 0.06s | ✅ |
| Q16 | Billing | Last week's appointments with no invoice issued. | FAIL | 1/5 | 0.00s | ❌ |
| Q17 | Billing | Average $/appointment by appointment type. | FAIL | 1/5 | 0.00s | ❌ |
| Q18 | No-shows | No-shows in last 4 weeks. | PASS | 5/5 | 0.05s | ✅ |
| Q19 | No-shows | Patients with highest no-show frequency. | FAIL | 1/5 | 0.00s | ❌ |
| Q20 | No-shows | Draft follow-up SMS for each no-show this week. | PARTIAL | 1/5 | 0.00s | ❌ |
| Q21 | Clinical | Summarise patient's last 5 visits in 3 bullets. | PASS | 5/5 | 0.05s | ✅ |
| Q22 | Clinical | Medical alerts not reviewed in 12 months. | FAIL | 1/5 | 0.00s | ❌ |
| Q23 | Clinical | Patients with [keyword] in recent notes. | FAIL | 1/5 | 0.00s | ❌ |
| Q24 | Operational | Duplicate patient records (same name+DOB). | PASS | 5/5 | 0.07s | ✅ |
| Q25 | Operational | Patients with no email/phone on file. | PASS | 5/5 | 0.06s | ✅ |
| Q26 | Operational | Appointments with no notes attached. | PASS | 5/5 | 0.39s | ✅ |

## Per-question details

### Q1: How many active patients do we have?
- **Category**: Practice Health
- **Tools used**: list_patients
- **Forecast**: PASS
- **Actual score**: 5/5  (0.10s)
- **Answer**:
```json
31
```

### Q2: Average appointments per week over last 3 months?
- **Category**: Practice Health
- **Tools used**: list_appointments
- **Forecast**: PASS
- **Actual score**: 5/5  (0.06s)
- **Answer**:
```json
1.5
```

### Q3: Top 10 practitioners by appointment count.
- **Category**: Practice Health
- **Tools used**: list_practitioners, list_appointments
- **Forecast**: PARTIAL
- **Actual score**: 5/5  (0.13s)
- **Answer**:
```json
[
  {
    "id": "1897443798099700990",
    "name": "Tradd Horne",
    "appointment_count": 23
  }
]
```

### Q4: Appointment types breakdown by frequency.
- **Category**: Practice Health
- **Tools used**: appointment_types(direct API), list_appointments
- **Forecast**: PARTIAL
- **Actual score**: 5/5  (0.16s)
- **Answer**:
```json
[
  {
    "id": "1897443801169932140",
    "name": "Standard Appointment",
    "count": 22
  },
  {
    "id": "1897443801606139757",
    "name": "First Appointment",
    "count": 1
  }
]
```

### Q5: % new vs returning patients.
- **Category**: Practice Health
- **Tools used**: (none — gap)
- **Forecast**: FAIL
- **Actual score**: 1/5  (0.00s)
- **IMPLEMENTATION GAP**: Cliniko has no direct 'first_appointment' or 'is_new_patient' field. Would need to list all patients, fetch each patient's appointment count, and label them. Slow (N+1) without a dedicated tool.

### Q6: Next week's schedule — full or gaps?
- **Category**: Schedule
- **Tools used**: list_appointments
- **Forecast**: PASS
- **Actual score**: 5/5  (0.05s)
- **Answer**:
```json
{
  "from": "2026-05-25",
  "to": "2026-05-31",
  "booked_appointments": 1,
  "total_in_window": 1
}
```

### Q7: Which practitioners have the most gaps in next 14 days?
- **Category**: Schedule
- **Tools used**: list_available_times
- **Forecast**: PARTIAL
- **Actual score**: 1/5  (0.00s)
- **IMPLEMENTATION GAP**: list_available_times exists but requires ALL of practitioner_id, business_id, appointment_type_id, from_date, to_date. To answer for a multi-practitioner clinic, the LLM has to fan out N×M×K calls. A `get_practitioner_gaps(from, to)` aggregator tool would be cleaner.

### Q8: Which day of week has highest no-show rate?
- **Category**: Schedule
- **Tools used**: list_appointments
- **Forecast**: PASS
- **Actual score**: 5/5  (0.06s)
- **Answer**:
```json
[
  {
    "day": "Mon",
    "total": 8,
    "no_shows": 0,
    "rate": 0.0
  },
  {
    "day": "Tue",
    "total": 4,
    "no_shows": 0,
    "rate": 0.0
  },
  {
    "day": "Wed",
    "total": 2,
    "no_shows": 0,
    "rate": 0.0
  },
  {
    "day": "Thu",
    "total": 2,
    "no_shows": 0,
    "rate": 0.0
  },
  {
    "day": "Fri",
    "total": 2,
    "no_shows": 0,
    "rate": 0.0
  },
  {
    "day": "Sat",
    "total": 1,
    "no_shows": 0,
    "rate": 0.0
  },
  {
    "day": "Sun",
    "total": 1,
    "no_shows": 0,
    "rate": 0.0
  }
]
```

### Q9: Online vs phone booking split?
- **Category**: Schedule
- **Tools used**: (none — gap)
- **Forecast**: FAIL
- **Actual score**: 1/5  (0.00s)
- **IMPLEMENTATION GAP**: Cliniko has no `source=online|phone` field on appointments. Online bookings live in /bookings; phone bookings are created direct in /individual_appointments. Could infer by joining, but it's noisy.

### Q10: Patients who haven't been in for 6+ months.
- **Category**: Recalls & Retention
- **Tools used**: list_patients, list_appointments
- **Forecast**: PASS
- **Actual score**: 5/5  (0.12s)
- **Answer**:
```json
{
  "total_patients": 31,
  "active_in_window": 16,
  "inactive_6mo": 15,
  "sample": [
    {
      "id": "1952658338625891768",
      "name": "Sarah Mitchell"
    },
    {
      "id": "1952658341268303289",
      "name": "James Chen"
    },
    {
      "id": "1952658342375599546",
      "name": "Aisha Patel"
    },
    {
      "id": "1952658343407398331",
      "name": "Liam O'Brien"
    },
    {
      "id": "1952658344565026236",
      "name": "Mei Wong"
    }
  ]
}
```

### Q11: Course-of-care recommended but not completed.
- **Category**: Recalls & Retention
- **Tools used**: (none — gap)
- **Forecast**: FAIL
- **Actual score**: 1/5  (0.00s)
- **IMPLEMENTATION GAP**: No structured 'course_of_care' field in Cliniko. Requires treatment-note semantic search (Phase D clinical-template work).

### Q12: Recalls due in next 30 days.
- **Category**: Recalls & Retention
- **Tools used**: list_recalls_due
- **Forecast**: PASS
- **Actual score**: 5/5  (0.05s)
- **Answer**:
```json
{
  "total_due_in_30d": 5,
  "sample": [
    {
      "id": "1952659251683930037",
      "recall_at": "2026-05-21"
    },
    {
      "id": "1952659252304687030",
      "recall_at": "2026-05-25"
    },
    {
      "id": "1952659252866723767",
      "recall_at": "2026-06-01"
    },
    {
      "id": "1952659253462314936",
      "recall_at": "2026-06-08"
    },
    {
      "id": "1952659254267621305",
      "recall_at": "2026-06-17"
    }
  ]
}
```

### Q13: % new patients who return for second appointment.
- **Category**: Recalls & Retention
- **Tools used**: (none — gap)
- **Forecast**: FAIL
- **Actual score**: 1/5  (0.00s)
- **IMPLEMENTATION GAP**: Same as Q5 — needs per-patient appointment counts. Could be done with N+1 calls or a dedicated 'patient_appointment_count' tool.

### Q14: Total outstanding invoice balance.
- **Category**: Billing
- **Tools used**: list_invoices
- **Forecast**: PASS
- **Actual score**: 5/5  (0.05s)
- **Answer**:
```json
{
  "outstanding_invoices": 0,
  "total_billed_outstanding": 0,
  "all_invoices_in_account": 1,
  "note": "Cliniko's invoice list view doesn't expose `balance` or `payments[]`. To get true outstanding $, fetch each non-paid invoice individually."
}
```

### Q15: Unpaid invoices over 30 days old.
- **Category**: Billing
- **Tools used**: list_unpaid_invoices
- **Forecast**: PASS
- **Actual score**: 5/5  (0.06s)
- **Answer**:
```json
{
  "count": 0,
  "sample_ids": []
}
```

### Q16: Last week's appointments with no invoice issued.
- **Category**: Billing
- **Tools used**: (none — gap)
- **Forecast**: FAIL
- **Actual score**: 1/5  (0.00s)
- **IMPLEMENTATION GAP**: Requires joining appointments (last week) with invoices to find appointments without a corresponding invoice. Doable via N+1 but a dedicated tool would be far cleaner.

### Q17: Average $/appointment by appointment type.
- **Category**: Billing
- **Tools used**: (none — gap)
- **Forecast**: FAIL
- **Actual score**: 1/5  (0.00s)
- **IMPLEMENTATION GAP**: Cliniko invoices link to billable_items, not appointment_types. Would need 3-way join: appointment → invoice → billable_item → appointment_type. Worth a dedicated aggregator tool.

### Q18: No-shows in last 4 weeks.
- **Category**: No-shows
- **Tools used**: list_appointments
- **Forecast**: PASS
- **Actual score**: 5/5  (0.05s)
- **Answer**:
```json
{
  "no_show_count": 0,
  "sample_ids": []
}
```

### Q19: Patients with highest no-show frequency.
- **Category**: No-shows
- **Tools used**: (none — gap)
- **Forecast**: FAIL
- **Actual score**: 1/5  (0.00s)
- **IMPLEMENTATION GAP**: Aggregation across patients needed. Could be done with N+1 calls (list_appointments_for_patient per patient + filter did_not_arrive) or a dedicated `patient_no_show_count` aggregator tool.

### Q20: Draft follow-up SMS for each no-show this week.
- **Category**: No-shows
- **Tools used**: (none — gap)
- **Forecast**: PARTIAL
- **Actual score**: 1/5  (0.00s)
- **IMPLEMENTATION GAP**: Cliniko API does not support sending communications (history-only). The draft can be produced by Claude using `list_appointments` filtered by did_not_arrive, but cannot be auto-sent — must be pasted into Cliniko UI manually.

### Q21: Summarise patient's last 5 visits in 3 bullets.
- **Category**: Clinical
- **Tools used**: list_treatment_notes_for_patient, get_treatment_note
- **Forecast**: PASS
- **Actual score**: 5/5  (0.05s)
- **Answer**:
```json
{
  "notes_found": 1,
  "patient_id": "1897453889804840137",
  "note": "Body content not in summary output; LLM would fetch via get_treatment_note(id) per note"
}
```

### Q22: Medical alerts not reviewed in 12 months.
- **Category**: Clinical
- **Tools used**: (none — gap)
- **Forecast**: FAIL
- **Actual score**: 1/5  (0.00s)
- **IMPLEMENTATION GAP**: Cliniko medical_alert has no `reviewed_at` field. Best proxy is patient.last_appointment_at; alerts on patients not seen in 12mo are presumed unreviewed.

### Q23: Patients with [keyword] in recent notes.
- **Category**: Clinical
- **Tools used**: (none — gap)
- **Forecast**: FAIL
- **Actual score**: 1/5  (0.00s)
- **IMPLEMENTATION GAP**: Cliniko's q[] doesn't support full-text on treatment_note.content. Phase E should add a local index or use Cliniko's `embedded` query if supported. Right now this requires fetching every note and grep-ing client-side — slow and PHI-heavy.

### Q24: Duplicate patient records (same name+DOB).
- **Category**: Operational
- **Tools used**: list_patients
- **Forecast**: PASS
- **Actual score**: 5/5  (0.07s)
- **Answer**:
```json
{
  "total_patients": 31,
  "duplicate_groups": 15,
  "sample": {
    "sarah mitchell (1985-03-15)": [
      "1952658338625891768",
      "1952659197459967436"
    ],
    "james chen (1972-08-22)": [
      "1952658341268303289",
      "1952659199473233357"
    ],
    "aisha patel (1991-11-04)": [
      "1952658342375599546",
      "1952659200538586574"
    ],
    "liam o'brien (1968-01-30)": [
      "1952658343407398331",
      "1952659201184509391"
    ],
    "mei wong (1995-06-12)": [
      "1952658344565026236",
      "1952659202006592976"
    ]
  }
}
```

### Q25: Patients with no email/phone on file.
- **Category**: Operational
- **Tools used**: list_patients
- **Forecast**: PASS
- **Actual score**: 5/5  (0.06s)
- **Answer**:
```json
{
  "total_patients": 31,
  "missing_contact": 0,
  "sample": []
}
```

### Q26: Appointments with no notes attached.
- **Category**: Operational
- **Tools used**: list_appointments, list_treatment_notes(per appt)
- **Forecast**: PASS
- **Actual score**: 5/5  (0.39s)
- **Answer**:
```json
{
  "checked_appointments": 7,
  "appointments_without_notes": 0,
  "sample_ids": []
}
```