"""Shared async HTTP client for Cliniko.

Design notes:
- ONE httpx.AsyncClient per tenant credential, reused across all tool calls.
  Per-call instantiation (which two of the reference implementations do) defeats
  connection pooling and leaks file handles.
- Exponential backoff with jitter on 429 and 5xx.
- Status-specific error shaping via `errors.py` so the LLM gets actionable responses.
- Cliniko IDs are 19-digit strings, not integers — never coerce to int.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from au_cliniko_mcp.auth import ClinikoCredential
from au_cliniko_mcp.errors import (
    LLMError,
    not_found,
    rate_limited,
    unauthorized,
    upstream_unavailable,
)

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 4
RETRY_STATUSES = {429, 500, 502, 503, 504}


class ClinikoClient:
    """Async wrapper over the Cliniko REST API."""

    def __init__(self, credential: ClinikoCredential, *, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self._credential = credential
        self._http = httpx.AsyncClient(
            base_url=credential.base_url,
            auth=(credential.api_key, ""),  # Cliniko uses HTTP Basic Auth: key as username, blank password
            headers={
                "User-Agent": credential.user_agent,
                "Accept": "application/json",
            },
            timeout=timeout,
        )

    @property
    def shard(self) -> str:
        return self._credential.shard

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "ClinikoClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any] | LLMError:
        return await self._request("GET", path, params=params)

    async def post(
        self, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any] | LLMError:
        return await self._request("POST", path, json=json)

    async def patch(
        self, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any] | LLMError:
        return await self._request("PATCH", path, json=json)

    async def delete(self, path: str) -> dict[str, Any] | LLMError:
        return await self._request("DELETE", path)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any] | LLMError:
        """Send a request with retry on rate-limit / 5xx, return parsed JSON or LLMError."""
        last_response: httpx.Response | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self._http.request(method, path, params=params, json=json)
            except httpx.RequestError as exc:
                # Network error — retry with backoff
                if attempt == MAX_RETRIES:
                    return upstream_unavailable(0).to_dict() | {"network_error": str(exc)}
                await self._sleep_with_backoff(attempt)
                continue

            if response.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
                last_response = response
                retry_after = _parse_retry_after(response) or self._backoff_delay(attempt)
                await asyncio.sleep(retry_after)
                continue

            return self._handle_response(response, path=path)

        # All retries exhausted on a retryable status
        if last_response is not None:
            if last_response.status_code == 429:
                retry_after = _parse_retry_after(last_response) or 60
                return rate_limited(retry_after).to_dict()
            return upstream_unavailable(last_response.status_code).to_dict()

        return upstream_unavailable(0).to_dict()

    def _handle_response(self, response: httpx.Response, *, path: str) -> dict[str, Any] | dict:
        if response.is_success:
            if response.status_code == 204:
                return {"status": "ok", "no_content": True}
            return response.json()

        status = response.status_code
        if status == 401:
            return unauthorized().to_dict()
        if status == 404:
            # Try to extract the resource type + id from the path for a useful error
            parts = [p for p in path.split("/") if p]
            resource = parts[0] if parts else "resource"
            resource_id = parts[1] if len(parts) >= 2 else "?"
            return not_found(resource, resource_id).to_dict()
        if status == 429:
            retry_after = _parse_retry_after(response) or 60
            return rate_limited(retry_after).to_dict()
        if 500 <= status < 600:
            return upstream_unavailable(status).to_dict()

        # 4xx other than handled cases — bubble Cliniko's own error body
        body: Any
        try:
            body = response.json()
        except Exception:
            body = response.text[:500]
        return LLMError(
            error="cliniko_error",
            what_happened=f"Cliniko returned {status} for {path}",
            what_to_do="Check the upstream message and adjust the request.",
            extra={"upstream_status": status, "upstream_body": body},
        ).to_dict()

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """Exponential backoff: 1, 2, 4, 8 seconds + up to 500ms jitter."""
        return (2**attempt) + random.uniform(0, 0.5)

    async def _sleep_with_backoff(self, attempt: int) -> None:
        await asyncio.sleep(self._backoff_delay(attempt))


def _parse_retry_after(response: httpx.Response) -> int | None:
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        return int(header)
    except ValueError:
        return None
