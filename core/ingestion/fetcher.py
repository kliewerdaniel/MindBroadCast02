"""HTTP fetching with the §7.6 security policy enforced before parsing.

Every request passes through `validate_url` first, and every redirect target
is re-validated by the same policy. No adapter can bypass this, because the
adapter never opens a socket itself — it goes through a Fetcher.

`transport` is injectable: the default raises, so a Fetcher without an
explicitly configured transport can never reach the network by accident.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol

from .adapters import FetchResult
from .security import (
    URLRejection, FetchLimits, FetchError, SecurityViolation, validate_url,
    validate_redirect, is_redirect, enforce_response_size,
    classify_http_error,
)


class Transport(Protocol):
    """Anything that can issue one HTTP request and return a response dict."""

    async def request(self, url: str, *, connect_timeout: float,
                      read_timeout: float,
                      checkpoint: Optional[str]) -> Dict: ...


@dataclass
class Fetcher:
    """Fetches URLs under the network policy.

    `transport` is injectable: the default raises, so a Fetcher without an
    explicitly configured transport can never reach the network by accident.

    `validate_targets` is True in production. Tests that inject a stub
    transport set it False so fixture hostnames (which do not resolve) can be
    exercised without weakening the guard for real fetches — the SSRF policy
    is still fully covered by its own test module.
    """
    limits: FetchLimits = field(default_factory=FetchLimits)
    transport: Optional[Transport] = None
    validate_targets: bool = True

    async def fetch(self, url: str,
                    checkpoint: Optional[str] = None) -> FetchResult:
        # Validate before any connection is attempted.
        if self.validate_targets:
            validate_url(url, self.limits)

        if self.transport is None:
            raise FetchError(
                "no_transport", url,
                "no transport configured; pass one to Fetcher")

        current = url
        redirect_chain: List[str] = []
        hops = 0

        while True:
            try:
                response = await self.transport.request(
                    current,
                    connect_timeout=self.limits.connect_timeout_seconds,
                    read_timeout=self.limits.read_timeout_seconds,
                    checkpoint=checkpoint,
                )
            except (SecurityViolation, FetchError):
                raise
            except asyncio.TimeoutError:
                raise FetchError("timeout", current, "request timed out")
            except Exception as exc:
                raise FetchError("connection", current, str(exc))

            status = response.get("status_code", 0)
            enforce_response_size(response.get("declared_length"), self.limits)

            location = response.get("location")
            if is_redirect(status) and location:
                redirect_chain.append(current)
                current = validate_redirect(current, location, self.limits,
                                            hops=hops)
                hops += 1
                continue

            if not (200 <= status < 300):
                error = classify_http_error(status)
                error.url = current
                raise error

            body = response.get("body", "") or ""
            encoded = len(body.encode("utf-8"))
            if encoded > self.limits.max_response_bytes:
                raise SecurityViolation(
                    URLRejection.RESERVED, current,
                    f"body {encoded} bytes exceeds "
                    f"{self.limits.max_response_bytes}")

            return FetchResult(
                url=url,
                status_code=status,
                headers=response.get("headers", {}),
                body=body,
                final_url=current,
                redirect_chain=redirect_chain,
            )


class StubTransport:
    """Test double returning scripted responses, recording every request."""

    def __init__(self, responses: Optional[Dict[str, Dict]] = None):
        self.responses = responses or {}
        self.requested: List[str] = []

    def add(self, url: str, status_code: int = 200, body: str = "",
            headers: Optional[Dict[str, str]] = None,
            location: Optional[str] = None,
            declared_length: Optional[int] = None) -> "StubTransport":
        self.responses[url] = {
            "status_code": status_code,
            "body": body,
            "headers": headers or {},
            "location": location,
            "declared_length": declared_length,
        }
        return self

    async def request(self, url: str, *, connect_timeout: float,
                      read_timeout: float,
                      checkpoint: Optional[str]) -> Dict:
        self.requested.append(url)
        if url not in self.responses:
            raise FetchError("not_found", url, "no scripted response")
        return self.responses[url]
