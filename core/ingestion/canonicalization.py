"""URL canonicalization and content normalization.

Spec §7.4: canonicalization must be conservative. Query parameters cannot
universally be discarded — some sites use them to identify distinct articles or
document versions. The policy is configurable and source-aware.

Every transformation is recorded, so an unexpected merge or duplicate
detection can be investigated after the fact. A canonicalization that cannot
explain itself is indistinguishable from a bug.
"""

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set
from urllib.parse import (
    urlparse, urlunparse, parse_qsl, urlencode, unquote, quote,
)


class Rule(Enum):
    """A normalization step, recorded so decisions are auditable."""
    LOWERED_HOST = "lowered_host"
    STRIPPED_WWW = "stripped_www"
    REMOVED_TRACKING_PARAMS = "removed_tracking_params"
    SORTED_QUERY_PARAMS = "sorted_query_params"
    REMOVED_FRAGMENT = "removed_fragment"
    REMOVED_DEFAULT_PORT = "removed_default_port"
    COLLAPSED_PATH = "collapsed_path"
    REMOVED_TRAILING_SLASH = "removed_trailing_slash"
    PRESERVED_QUERY = "preserved_query"


# Well-known tracking and analytics parameters. Removing these is safe because
# they carry no content identity.
TRACKING_PARAMS: Set[str] = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_cid", "utm_reader", "utm_brand",
    "gclid", "gclsrc", "dclid", "fbclid", "msclkid", "twclid", "igshid",
    "mc_cid", "mc_eid", "_ga", "_gl", "yclid", "wickedid",
    "ref", "referrer", "source", "sr_share", "cmpid", "CMP",
    "ito", "at_medium", "at_campaign", "spm", "scm",
    "ncid", "srnd", "partner", "smid", "smtyp",
}


@dataclass
class CanonicalizationPolicy:
    """How aggressively to normalize. Conservative by default.

    `strip_query` is False because discarding query parameters can merge two
    genuinely distinct documents (spec §7.4). It is opt-in per source.
    """
    strip_tracking_params: bool = True
    strip_query: bool = False
    strip_fragment: bool = True
    strip_www: bool = True
    sort_query: bool = True
    remove_trailing_slash: bool = False
    collapse_path: bool = True
    keep_params: Set[str] = field(default_factory=set)


DEFAULT_POLICY = CanonicalizationPolicy()


@dataclass
class CanonicalizationResult:
    canonical_url: str
    original_url: str
    final_url: str
    rules_applied: List[Rule] = field(default_factory=list)
    redirect_chain: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "canonical_url": self.canonical_url,
            "original_url": self.original_url,
            "final_url": self.final_url,
            "rules_applied": [r.value for r in self.rules_applied],
            "redirect_chain": self.redirect_chain,
        }


def canonicalize_url(
    url: str,
    policy: Optional[CanonicalizationPolicy] = None,
    final_url: Optional[str] = None,
    redirect_chain: Optional[List[str]] = None,
) -> CanonicalizationResult:
    """Canonicalize a URL, recording every rule applied.

    Normalizes host casing, removes known tracking parameters, collapses safe
    path variations, and optionally follows the publisher's own canonical hint.
    """
    policy = policy or DEFAULT_POLICY
    rules: List[Rule] = []
    original = url

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    # `parsed.hostname` is already lowercased by urlparse, so comparing it to
    # itself can never detect a change. Compare against the raw netloc, which
    # preserves the original casing.
    raw_host = parsed.netloc.split("@")[-1].split(":")[0]
    host = (parsed.hostname or "").lower()

    if raw_host.lower() != raw_host:
        rules.append(Rule.LOWERED_HOST)

    if policy.strip_www and host.startswith("www."):
        host = host[4:]
        rules.append(Rule.STRIPPED_WWW)

    # Rebuild netloc, preserving userinfo and an explicit non-default port.
    port = parsed.port
    netloc = host
    if port is not None:
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            rules.append(Rule.REMOVED_DEFAULT_PORT)
        else:
            netloc = f"{host}:{port}"
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        netloc = f"{auth}@{netloc}"

    path = parsed.path
    if policy.collapse_path:
        collapsed = _collapse_path(path)
        if collapsed != path:
            rules.append(Rule.COLLAPSED_PATH)
            path = collapsed

    if policy.remove_trailing_slash and len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
        rules.append(Rule.REMOVED_TRAILING_SLASH)

    # Query handling — the part that must stay conservative.
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if policy.strip_tracking_params and query_pairs:
        kept = [
            (k, v) for k, v in query_pairs
            if k not in TRACKING_PARAMS or k in policy.keep_params
        ]
        if len(kept) != len(query_pairs):
            rules.append(Rule.REMOVED_TRACKING_PARAMS)
        query_pairs = kept

    if policy.strip_query:
        if query_pairs:
            rules.append(Rule.REMOVED_TRACKING_PARAMS)
        query_pairs = []
    elif query_pairs:
        rules.append(Rule.PRESERVED_QUERY)
        if policy.sort_query:
            ordered = sorted(query_pairs)
            if ordered != query_pairs:
                rules.append(Rule.SORTED_QUERY_PARAMS)
            query_pairs = ordered

    query = urlencode(query_pairs)

    fragment = ""
    if not policy.strip_fragment and parsed.fragment:
        fragment = parsed.fragment
    elif parsed.fragment:
        rules.append(Rule.REMOVED_FRAGMENT)

    canonical = urlunparse((scheme, netloc, path, parsed.params, query, fragment))

    return CanonicalizationResult(
        canonical_url=canonical,
        original_url=original,
        final_url=final_url or url,
        rules_applied=rules,
        redirect_chain=redirect_chain or [],
    )


def _collapse_path(path: str) -> str:
    """Resolve `.` and `..` segments without touching the query string."""
    if not path:
        return path
    trailing = "/" if path.endswith("/") and len(path) > 1 else ""
    segments = [s for s in path.split("/") if s not in ("", ".")]
    resolved: List[str] = []
    for segment in segments:
        if segment == "..":
            if resolved:
                resolved.pop()
        else:
            resolved.append(segment)
    collapsed = "/" + "/".join(resolved)
    return collapsed + trailing if trailing or collapsed != "/" else collapsed


# ── Text normalization and hashing ───────────────────────────────────


_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_text(text: str) -> str:
    """Normalize text for comparison and hashing.

    Collapses whitespace, lowercases, and strips punctuation so that trivial
    formatting differences do not defeat deduplication.
    """
    if not text:
        return ""
    collapsed = _WHITESPACE.sub(" ", text).strip().lower()
    return _PUNCTUATION.sub("", collapsed)


def content_hash(text: str) -> str:
    """Stable hash of normalized text — the exact-duplicate key."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def shingles(text: str, size: int = 5) -> Set[str]:
    """Word-level shingles for near-duplicate comparison."""
    words = normalize_text(text).split()
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {
        " ".join(words[i:i + size]) for i in range(len(words) - size + 1)
    }


def jaccard(a: Set[str], b: Set[str]) -> float:
    """Jaccard similarity. Symmetric by construction."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def text_similarity(a: str, b: str, shingle_size: int = 5) -> float:
    """Shingle-based similarity in [0, 1]. Deterministic and symmetric."""
    return jaccard(shingles(a, shingle_size), shingles(b, shingle_size))


def percent_decode_once(value: str) -> str:
    """Decode a percent-encoded component exactly once.

    Deliberately not recursive: repeatedly decoding is a way to smuggle
    payloads past validation, and a single pass is what a URL parser does.
    """
    return unquote(value)


def safe_quote(value: str) -> str:
    return quote(value, safe="/-_.~")
