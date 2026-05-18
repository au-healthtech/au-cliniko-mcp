"""End-to-end smoke test against a real Cliniko account.

Run with:
    set -a; source .env; set +a
    PYTHONPATH=src python tests/integration/smoke_test.py

Exercises the credential parser, the client, and a handful of read tools.
Designed for a fresh trial account with no patient data — verifies the
empty-state paths work, not just the populated ones.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Make the src/ package importable when run from the repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from au_cliniko_mcp.auth import ClinikoCredential
from au_cliniko_mcp.client import ClinikoClient


async def main() -> None:
    api_key = os.environ["CLINIKO_API_KEY"]
    email = os.environ["CLINIKO_USER_AGENT_EMAIL"]

    print("=" * 60)
    print("1. CREDENTIAL PARSING")
    print("=" * 60)
    cred = ClinikoCredential.from_env(api_key=api_key, user_agent_email=email)
    print(f"  shard      : {cred.shard}")
    print(f"  base_url   : {cred.base_url}")
    print(f"  user_agent : {cred.user_agent}")
    print(f"  api_key    : {cred.api_key[:8]}…{cred.api_key[-8:]}")

    async with ClinikoClient(cred) as client:
        print()
        print("=" * 60)
        print("2. AUTH CHECK — GET /businesses (lightweight)")
        print("=" * 60)
        result = await client.get("/businesses")
        if isinstance(result, dict) and result.get("error"):
            print(f"  ❌ ERROR: {json.dumps(result, indent=2)}")
            sys.exit(1)
        businesses = result.get("businesses", [])
        print(f"  ✅ Authenticated. {len(businesses)} business(es) on account.")
        for b in businesses:
            print(
                f"    - id={b.get('id')} name={b.get('business_name')!r} "
                f"city={b.get('city')!r} country={b.get('country')!r}"
            )

        print()
        print("=" * 60)
        print("3. GET /practitioners")
        print("=" * 60)
        result = await client.get("/practitioners")
        if result.get("error"):
            print(f"  ❌ ERROR: {result}")
        else:
            practs = result.get("practitioners", [])
            print(f"  ✅ {len(practs)} practitioner(s) on account.")
            for p in practs:
                print(
                    f"    - id={p.get('id')} name="
                    f"{(p.get('first_name') or '')} {(p.get('last_name') or '')!r} "
                    f"active={p.get('active')}"
                )

        print()
        print("=" * 60)
        print("4. GET /patients (expecting empty on a fresh trial)")
        print("=" * 60)
        result = await client.get("/patients", params={"per_page": 5})
        if result.get("error"):
            print(f"  ❌ ERROR: {result}")
        else:
            patients = result.get("patients", [])
            total = result.get("total_entries", "?")
            print(f"  ✅ {len(patients)} patient(s) returned, total_entries={total}")

        print()
        print("=" * 60)
        print("5. GET /appointment_types")
        print("=" * 60)
        result = await client.get("/appointment_types")
        if result.get("error"):
            print(f"  ❌ ERROR: {result}")
        else:
            types = result.get("appointment_types", [])
            print(f"  ✅ {len(types)} appointment type(s) configured.")
            for t in types[:5]:
                print(f"    - id={t.get('id')} name={t.get('name')!r}")

        print()
        print("=" * 60)
        print("6. AUTH NEGATIVE — bad path should 404 cleanly")
        print("=" * 60)
        result = await client.get("/nonexistent_endpoint")
        if result.get("error"):
            print(f"  ✅ Error correctly returned: {result.get('error')}")
        else:
            print(f"  ⚠️  Unexpected success: {result}")

    print()
    print("Smoke test complete.")


if __name__ == "__main__":
    asyncio.run(main())
