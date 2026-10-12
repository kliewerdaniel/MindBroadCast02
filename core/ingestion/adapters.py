"""Source adapters.

Spec §7.2: each connector implements a common interface with explicit error
reporting, checkpoint handling, and normalized output.

Two requirements shape this module:

1. **The ingestion system must not assume every source exposes a reliable
   publication timestamp or stable identifier.** When information is missing,
   the system preserves that uncertainty rather than inventing values. So
   `published_at` and `guid` are `Optional`, and a missing timestamp stays
   `None` — it is never defaulted to "now", which would fabricate an
   observation time and corrupt the three time axes (§5.9.1, ADR-012).

2. **A malformed feed must not crash the application** (§7.7 criterion 2).
   Parse failures raise `AdapterError`, which the scheduler classifies.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class AdapterError(Exception):
    """A source could not be fetched or parsed."""

    def __init__(self, category: str, detail: str = "", transient: bool = True):
        self.category = category
        self.detail = detail
        self.transient = transient
        super().__init__(f"{category}: {detail}" if detail else category)


@dataclass
class NormalizedEntry:
    """One feed item in normalized form.

    Every field that a source may legitimately omit is Optional and stays
    None when absent.
    """
    title: str = ""
    link: str = ""
    guid: Optional[str] = None
    published_at: Optional[datetime] = None      # never invented
    updated_at: Optional[datetime] = None
    author: Optional[str] = None
    summary: str = ""
    content: str = ""
    language: Optional[str] = None
    categories: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchResult:
    """The outcome of one fetch attempt."""
    url: str
    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: str = ""
    final_url: Optional[str] = None
    redirect_chain: List[str] = field(default_factory=list)
    fetched_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


@dataclass
class HealthStatus:
    healthy: bool
    detail: str = ""
    last_checked: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))


class SourceAdapter(ABC):
    """Common connector contract (spec §7.2)."""

    @abstractmethod
    async def fetch_updates(self, cursor: Optional[str] = None) -> FetchResult:
        """Fetch raw updates since `cursor`."""

    @abstractmethod
    def parse_response(self, response: FetchResult) -> List[NormalizedEntry]:
        """Parse a fetch result into normalized entries."""

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Report whether the source is reachable."""

    @abstractmethod
    def get_checkpoint(self) -> Optional[str]:
        """The cursor marking what has been consumed."""

    @abstractmethod
    def restore_checkpoint(self, checkpoint: Optional[str]) -> None:
        """Restore a previously stored cursor."""


class FeedAdapter(SourceAdapter):
    """RSS and Atom adapter over feedparser.

    feedparser handles both formats, so one adapter covers the initial
    ingestion target (spec §7.1: prioritize RSS and Atom).
    """

    def __init__(self, fetcher, url: str):
        self.fetcher = fetcher
        self.url = url
        self._checkpoint: Optional[str] = None

    async def fetch_updates(self, cursor: Optional[str] = None) -> FetchResult:
        effective = cursor or self._checkpoint
        return await self.fetcher.fetch(self.url, checkpoint=effective)

    def parse_response(self, response: FetchResult) -> List[NormalizedEntry]:
        import feedparser

        if not response.ok:
            raise AdapterError(
                "bad_status", f"HTTP {response.status_code}",
                transient=response.status_code >= 500 or response.status_code == 429)

        try:
            parsed = feedparser.parse(response.body)
        except Exception as exc:  # feedparser is lenient, but be explicit
            raise AdapterError("parse_error", str(exc), transient=False)

        # feedparser sets bozo on malformed input but still returns entries.
        # A bozo feed with no entries at all is a failure, not an empty result.
        if parsed.get("bozo") and not parsed.get("entries"):
            detail = str(parsed.get("bozo_exception", "malformed feed"))
            raise AdapterError("malformed_feed", detail, transient=False)

        feed_language = _entry_language(parsed.get("feed"))
        entries: List[NormalizedEntry] = []
        for raw in parsed.get("entries", []):
            entries.append(_normalize_entry(raw, feed_language))
        return entries

    async def health_check(self) -> HealthStatus:
        try:
            result = await self.fetch_updates()
        except AdapterError as exc:
            return HealthStatus(healthy=False, detail=str(exc))
        except Exception as exc:
            return HealthStatus(healthy=False, detail=f"unexpected: {exc}")
        return HealthStatus(
            healthy=result.ok,
            detail=f"HTTP {result.status_code}")

    def get_checkpoint(self) -> Optional[str]:
        return self._checkpoint

    def restore_checkpoint(self, checkpoint: Optional[str]) -> None:
        self._checkpoint = checkpoint


def make_adapter(source_type: str, fetcher, url: str) -> SourceAdapter:
    """Construct an adapter for a source type.

    RSS and Atom share an implementation; the type is accepted so the
    registry can record what was configured without changing behaviour.
    """
    normalized = (source_type or "").lower()
    if normalized in ("rss", "atom"):
        return FeedAdapter(fetcher, url)
    raise AdapterError(
        "unsupported_source_type",
        f"no adapter for {source_type!r}",
        transient=False)


# ── entry normalization ──────────────────────────────────────────────


def _entry_language(feed: Optional[Dict[str, Any]]) -> Optional[str]:
    if not feed:
        return None
    return feed.get("language")


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse a feed timestamp, returning None when absent or unparseable.

    Never substitutes the current time — an unparseable timestamp is missing
    information, and inventing one would misrepresent when the source
    published (ADR-012).
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    import calendar
    import email.utils
    import time

    text = str(value).strip()
    # RFC 2822, the usual RSS form.
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(
                tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    # ISO 8601, the usual Atom form.
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        pass
    # feedparser's own struct_time.
    if hasattr(value, "tm_year"):
        return datetime.fromtimestamp(
            calendar.timegm(value), tz=timezone.utc)
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def _normalize_entry(raw: Dict[str, Any],
                     feed_language: Optional[str]) -> NormalizedEntry:
    link = _first_link(raw)
    published = _parse_timestamp(
        raw.get("published_parsed") or raw.get("published"))
    updated = _parse_timestamp(
        raw.get("updated_parsed") or raw.get("updated"))

    summary = _clean_html(raw.get("summary", "") or "")
    content = ""
    if raw.get("content"):
        parts = raw["content"]
        if isinstance(parts, list) and parts:
            content = _clean_html(parts[0].get("value", "") or "")

    return NormalizedEntry(
        title=(raw.get("title") or "").strip(),
        link=link,
        guid=raw.get("id") or raw.get("guid") or None,
        published_at=published,
        updated_at=updated,
        author=_author(raw),
        summary=summary,
        content=content,
        language=raw.get("language") or feed_language,
        categories=[t.get("term") for t in raw.get("tags", []) if t.get("term")],
        metadata={"raw_keys": sorted(raw.keys())},
    )


def _first_link(raw: Dict[str, Any]) -> str:
    links = raw.get("links") or []
    for link in links:
        if link.get("rel") in (None, "alternate") and link.get("href"):
            return link["href"]
    return raw.get("link", "") or ""


def _author(raw: Dict[str, Any]) -> Optional[str]:
    if raw.get("author"):
        return raw["author"]
    detail = raw.get("author_detail")
    if isinstance(detail, dict) and detail.get("name"):
        return detail["name"]
    return None


def _clean_html(text: str) -> str:
    from ..ingestion.security import strip_active_content
    return strip_active_content(text).strip()
