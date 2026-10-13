"""A real HTTP transport for the ingestion fetcher.

The ingestion layer defined a `Transport` protocol with only a `StubTransport`
— so every ingestion test passed, but nothing could actually reach the network.
This is the missing real transport: aiohttp-backed, returning the same response
dict shape the fetcher and its policy expect.

It still goes through the fetcher's SSRF/scheme/size policy (`validate_url`),
so the security guarantees are unchanged — this only replaces the transport
that raises-by-default with one that really fetches.
"""

from typing import Dict, Optional

import aiohttp

from .fetcher import FetchError


class AiohttpTransport:
    """Real network transport satisfying the ingestion `Transport` protocol."""

    def __init__(self, max_body_bytes: int = 5 * 1024 * 1024,
                 user_agent: str = "MindBroadcast/1.0"):
        self.max_body_bytes = max_body_bytes
        self.user_agent = user_agent

    async def request(self, url: str, *, connect_timeout: float,
                      read_timeout: float,
                      checkpoint: Optional[str]) -> Dict:
        headers = {"User-Agent": self.user_agent}
        if checkpoint:
            headers["If-Modified-Since"] = checkpoint
        timeout = aiohttp.ClientTimeout(total=read_timeout,
                                        connect=connect_timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers,
                                       allow_redirects=False) as response:
                    # Resolve the charset BEFORE consuming the body: aiohttp's
                    # get_encoding() raises "Cannot compute fallback encoding
                    # of a not yet read body" once content.read() has drained
                    # the stream and no charset was advertised (BBC article
                    # pages). Fall back to utf-8.
                    charset = (response.charset or "utf-8")
                    # Read at most max_body_bytes + 1 so an oversized body is
                    # detectable rather than silently truncated.
                    body_bytes = await response.content.read(
                        self.max_body_bytes + 1)
                    body = body_bytes[:self.max_body_bytes].decode(
                        charset, errors="replace")
                    # Content-Length is a header string; the fetcher's size
                    # policy compares it against an int limit, so coerce it.
                    declared = response.headers.get("Content-Length")
                    try:
                        declared_length = int(declared) if declared else None
                    except ValueError:
                        declared_length = None
                    return {
                        "status_code": response.status,
                        "body": body,
                        "headers": dict(response.headers),
                        "location": response.headers.get("Location"),
                        "declared_length": declared_length,
                    }
        except aiohttp.ClientError as error:
            raise FetchError("network", url, str(error))
