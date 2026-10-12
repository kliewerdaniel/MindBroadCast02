"""Fetch security for untrusted external sources.

Spec §7.6: external articles are untrusted input. They may contain malicious
HTML, tracking elements, misleading metadata, or prompt-injection instructions.
MindBroadcast must treat source content as data, never as an instruction.

These protections apply even to sources the system considers generally
reputable — a compromised or spoofed reputable feed is exactly the case that
matters.

The critical property: **redirect targets are re-validated against the same
policy.** A URL that passes validation on the first hop must not be able to
reach a loopback or metadata endpoint on the third.
"""

import ipaddress
import socket
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple
from urllib.parse import urlparse, urljoin


class URLRejection(Enum):
    """Why a URL was refused. Structured so callers can distinguish causes."""
    SCHEME = "scheme"
    NO_HOST = "no_host"
    LOOPBACK = "loopback"
    PRIVATE = "private"
    LINK_LOCAL = "link_local"
    RESERVED = "reserved"
    METADATA = "metadata"
    UNRESOLVABLE = "unresolvable"
    TOO_MANY_REDIRECTS = "too_many_redirects"


class SecurityViolation(Exception):
    """A fetch target violated the network policy."""

    def __init__(self, rejection: URLRejection, url: str, detail: str = ""):
        self.rejection = rejection
        self.url = url
        self.detail = detail
        super().__init__(
            f"{rejection.value}: {url}" + (f" ({detail})" if detail else "")
        )


ALLOWED_SCHEMES = frozenset({"http", "https"})

# Cloud metadata endpoints. 169.254.169.254 is the well-known one; the others
# are provider-specific and equally dangerous if reachable.
METADATA_HOSTS = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.goog",
    "100.100.100.200",          # Alibaba Cloud
})

# Networks that must never be reached through a configured or redirected URL.
BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),      # carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),        # loopback
    ipaddress.ip_network("169.254.0.0/16"),     # link-local + metadata
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("224.0.0.0/4"),        # multicast
    ipaddress.ip_network("240.0.0.0/4"),        # reserved
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
]

DEFAULT_MAX_REDIRECTS = 5


@dataclass
class FetchLimits:
    """Connection, size, and redirect bounds."""
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 20.0
    max_response_bytes: int = 10 * 1024 * 1024      # 10 MB
    max_redirects: int = DEFAULT_MAX_REDIRECTS
    allowed_schemes: frozenset = field(
        default_factory=lambda: ALLOWED_SCHEMES)


def _classify_address(ip_text: str) -> Optional[URLRejection]:
    """Return the rejection reason for an IP, or None if it is permitted."""
    try:
        address = ipaddress.ip_address(ip_text)
    except ValueError:
        return URLRejection.UNRESOLVABLE

    if address.is_loopback:
        return URLRejection.LOOPBACK
    # Metadata endpoints are checked before the generic link-local rule,
    # because every known metadata address sits inside 169.254.0.0/16 or is
    # otherwise link-local — the specific reason must win so operators can
    # tell a metadata probe from an ordinary link-local address.
    if ip_text in METADATA_HOSTS:
        return URLRejection.METADATA
    if address.is_link_local:
        return URLRejection.LINK_LOCAL
    if address.is_multicast or address.is_reserved or address.is_unspecified:
        return URLRejection.RESERVED
    if address.is_private:
        return URLRejection.PRIVATE
    return None


def _resolve_host(host: str) -> List[str]:
    """Resolve a hostname to IP addresses. Empty list if unresolvable."""
    # A literal IP needs no resolution.
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, socket.timeout, UnicodeError):
        return []
    return sorted({str(info[4][0]) for info in infos})


def validate_url(url: str, limits: Optional[FetchLimits] = None,
                 resolve: bool = True) -> None:
    """Validate a fetch target. Raises SecurityViolation if not permitted.

    `resolve=False` skips DNS resolution only. Literal IP addresses are still
    classified — otherwise `resolve=False` would silently permit
    `http://127.0.0.1/`, which is the single most dangerous bypass.
    """
    limits = limits or FetchLimits()

    parsed = urlparse(url)
    if parsed.scheme.lower() not in limits.allowed_schemes:
        raise SecurityViolation(
            URLRejection.SCHEME, url,
            f"scheme {parsed.scheme!r} not in {sorted(limits.allowed_schemes)}")

    host = parsed.hostname
    if not host:
        raise SecurityViolation(URLRejection.NO_HOST, url, "no host component")

    if host.lower() in METADATA_HOSTS:
        raise SecurityViolation(URLRejection.METADATA, url,
                                "cloud metadata endpoint")

    # A literal IP is checked directly, with or without DNS resolution.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        rejection = _classify_address(host)
        if rejection is not None:
            raise SecurityViolation(
                rejection, url, f"literal address {host}")
        return

    if not resolve:
        return

    addresses = _resolve_host(host)
    if not addresses:
        raise SecurityViolation(
            URLRejection.UNRESOLVABLE, url, f"cannot resolve {host}")

    for ip_text in addresses:
        rejection = _classify_address(ip_text)
        if rejection is not None:
            raise SecurityViolation(
                rejection, url, f"{host} resolves to {ip_text}")


def validate_redirect(
    current_url: str, location: str, limits: Optional[FetchLimits] = None,
    hops: int = 0, resolve: bool = True,
) -> str:
    """Resolve and validate a redirect target.

    Raises SecurityViolation if the redirect chain is too long or the target
    is not permitted. Returns the absolute target URL. The target is validated
    by exactly the same policy as the original URL — a permitted first hop
    must not be able to reach a blocked host on a later one.
    """
    limits = limits or FetchLimits()
    if hops >= limits.max_redirects:
        raise SecurityViolation(
            URLRejection.TOO_MANY_REDIRECTS, location,
            f"exceeded {limits.max_redirects} redirects from {current_url}")

    target = urljoin(current_url, location)
    validate_url(target, limits, resolve=resolve)
    return target


def is_redirect(status_code: int) -> bool:
    return status_code in (301, 302, 303, 307, 308)


def redirect_target(status_code: int, location: Optional[str]) -> Optional[str]:
    """The Location header for a redirect response, or None."""
    if not is_redirect(status_code) or not location:
        return None
    return location


def enforce_response_size(
    declared_length: Optional[int], limits: Optional[FetchLimits] = None
) -> None:
    """Reject a response whose declared length exceeds the cap."""
    limits = limits or FetchLimits()
    if declared_length is not None and declared_length > limits.max_response_bytes:
        raise SecurityViolation(
            URLRejection.RESERVED, "",
            f"declared length {declared_length} exceeds "
            f"{limits.max_response_bytes} bytes")


class FetchError(Exception):
    """A fetch failed for a reason the scheduler should classify."""

    def __init__(self, category: str, url: str, detail: str = "",
                 transient: bool = True):
        self.category = category
        self.url = url
        self.detail = detail
        self.transient = transient
        super().__init__(f"{category}: {url}" + (f" ({detail})" if detail else ""))


# Transient failures may be retried; permanent ones must not be hammered.
TRANSIENT_CATEGORIES = frozenset({"timeout", "connection", "server_error"})
PERMANENT_CATEGORIES = frozenset({
    "not_found", "forbidden", "unauthorized", "bad_request", "malformed",
})


def classify_http_error(status_code: int) -> FetchError:
    """Map an HTTP status to a categorized FetchError."""
    if status_code in (408, 429) or status_code >= 500:
        return FetchError("timeout" if status_code == 408 else "server_error",
                          "", f"HTTP {status_code}", transient=True)
    if status_code == 404:
        return FetchError("not_found", "", "HTTP 404", transient=False)
    if status_code in (401, 403):
        return FetchError("forbidden", "", f"HTTP {status_code}", transient=False)
    if status_code >= 400:
        return FetchError("bad_request", "", f"HTTP {status_code}",
                          transient=False)
    return FetchError("unknown", "", f"HTTP {status_code}", transient=True)


def strip_active_content(html: str) -> str:
    """Remove executable content from fetched HTML.

    Spec §7.6: strip or safely handle active HTML content; never execute
    scripts from fetched pages. This is a conservative text extraction, not a
    full sanitizer — the goal is that nothing fetched can act on the system.
    """
    import re
    # Remove script, style, and other active elements entirely, including
    # their contents.
    text = re.sub(
        r"(?is)<(script|style|iframe|object|embed|applet|form|noscript)\b.*?</\1>",
        " ", html)
    # Remove event-handler attributes, which are the common injection vector.
    text = re.sub(r'(?i)\son\w+\s*=\s*"[^"]*"', " ", text)
    text = re.sub(r"(?i)\son\w+\s*=\s*'[^']*'", " ", text)
    # Remove javascript: and data: URLs from href/src.
    text = re.sub(r'(?i)(href|src)\s*=\s*["\']?\s*(javascript|data|vbscript):[^"\'>\s]*',
                  r"\1=\"\"", text)
    # Drop remaining tags.
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return text


def looks_like_injection(text: str) -> bool:
    """Heuristic flag for prompt-injection attempts in source content.

    Not a defense on its own — the real control is that source text is always
    separated from system instructions (§14.5). This exists so the pipeline can
    *record* a suspicious payload for review.
    """
    import re
    patterns = [
        r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
        r"(?i)disregard\s+(all\s+)?(previous|prior|above)",
        r"(?i)you\s+are\s+now\s+(a|an)\b",
        r"(?i)system\s*:\s*you\s+must",
        r"(?i)<\|\s*im_start\s*\|>",
        r"(?i)\[\s*INST\s*\]",
        r"(?i)new\s+instructions?\s*:",
    ]
    return any(re.search(p, text) for p in patterns)
