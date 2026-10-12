"""Ingestion pipeline.

Spec §7.1: the flow from source configuration to the knowledge processing
queue.

```
Source Configuration → Polling Scheduler → Fetch Request → Response Validation
→ Format Parser → Canonicalization → Duplicate Detection → Durable Article
Capture → Ingestion Event → Knowledge Processing Queue
```

Two durability guarantees drive the design:

1. **A new article is durably stored before downstream processing is
   considered scheduled** (§7.7 criterion 4). The article row and its outbox
   message commit in one transaction, via `enqueue_outbox_in()` (ADR-013). A
   crash between them cannot leave an article with no scheduled work, or
   scheduled work for an article that does not exist.

2. **A restarted process can recover pending ingestion tasks** (criterion 8).
   Pending outbox rows are the recovery source of truth; the pipeline never
   treats an in-memory queue as the record of what remains to be done.

Ingestion does not determine editorial meaning (§7.1). No language model is
called anywhere in this module.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from ..models import (
    Source, SourceType, Article, ExtractionStatus,
)
from ..persistence import WorldStore
from .adapters import FeedAdapter, NormalizedEntry, AdapterError
from .canonicalization import (
    canonicalize_url, CanonicalizationPolicy, CanonicalizationResult,
    content_hash as hash_text,
)
from .deduplication import (
    Deduplicator, DeduplicationPolicy, DuplicateKind, DuplicateCandidate,
    ExistingArticle,
)
from .fetcher import Fetcher
from .scheduler import SourcePollState, decide_under_backpressure


class IngestDisposition(Enum):
    """What happened to one entry."""
    CAPTURED = "captured"              # new article stored
    SUPERSEDED = "superseded"          # same URL, changed content -> new version
    DUPLICATE = "duplicate"            # exact re-observation, discarded
    ASSOCIATED = "associated"          # near-dup / syndicated, recorded, retained
    SKIPPED_NO_URL = "skipped_no_url"  # entry had no link; nothing to capture
    FLAGGED = "flagged"                # stored but marked for review


@dataclass
class IngestOutcome:
    disposition: IngestDisposition
    entry_title: str = ""
    article_id: Optional[str] = None
    canonical_url: Optional[str] = None
    candidate: Optional[DuplicateCandidate] = None
    canonicalization: Optional[CanonicalizationResult] = None
    outbox_message_id: Optional[str] = None
    detail: str = ""


@dataclass
class IngestReport:
    source_id: str = ""
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    entries_seen: int = 0
    outcomes: List[IngestOutcome] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def counts(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for outcome in self.outcomes:
            key = outcome.disposition.value
            result[key] = result.get(key, 0) + 1
        return result


@dataclass
class IngestionPolicy:
    canonicalization: CanonicalizationPolicy = field(
        default_factory=CanonicalizationPolicy)
    deduplication: DeduplicationPolicy = field(
        default_factory=DeduplicationPolicy)
    schedule_knowledge_processing: bool = True


DEFAULT_POLICY = IngestionPolicy()


class IngestionPipeline:
    """Turns fetched feed entries into durable, deduplicated article records."""

    def __init__(
        self,
        store: WorldStore,
        fetcher: Fetcher,
        policy: Optional[IngestionPolicy] = None,
    ):
        self.store = store
        self.fetcher = fetcher
        self.policy = policy or DEFAULT_POLICY
        self.deduplicator = Deduplicator(self.policy.deduplication)

    # ── one source, one poll ─────────────────────────────

    async def ingest_source(self, source: Source) -> IngestReport:
        """Fetch and capture everything new from one source."""
        report = IngestReport(source_id=source.id)

        try:
            adapter = FeedAdapter(self.fetcher, source.endpoint)
            adapter.restore_checkpoint(None)
            result = await adapter.fetch_updates()
            entries = adapter.parse_response(result)
        except AdapterError as exc:
            report.error = f"{exc.category}: {exc.detail}"
            report.completed_at = datetime.now(timezone.utc)
            return report
        except Exception as exc:
            report.error = f"unexpected: {exc}"
            report.completed_at = datetime.now(timezone.utc)
            return report

        report.entries_seen = len(entries)

        existing = await self._existing_articles()
        for entry in entries:
            outcome = await self._ingest_entry(source, entry, existing)
            report.outcomes.append(outcome)
            # A newly captured article must be visible to later entries in the
            # same batch, or a feed repeating itself within one response would
            # capture the same article twice.
            if outcome.disposition in (IngestDisposition.CAPTURED,
                                       IngestDisposition.SUPERSEDED,
                                       IngestDisposition.ASSOCIATED,
                                       IngestDisposition.FLAGGED):
                existing = await self._existing_articles()

        report.completed_at = datetime.now(timezone.utc)
        return report

    # ── one entry ────────────────────────────────────────

    async def _ingest_entry(
        self,
        source: Source,
        entry: NormalizedEntry,
        existing: List[ExistingArticle],
    ) -> IngestOutcome:
        if not entry.link:
            return IngestOutcome(
                disposition=IngestDisposition.SKIPPED_NO_URL,
                entry_title=entry.title,
                detail="entry has no link; nothing to capture",
            )

        canonical = canonicalize_url(
            entry.link,
            self.policy.canonicalization,
            final_url=entry.link,
        )
        text = entry.content or entry.summary
        digest = hash_text(text)

        candidate = self.deduplicator.classify(
            canonical.canonical_url, text, source.id, existing,
            text_hash=digest,
        )

        if candidate.should_discard and candidate.kind is DuplicateKind.EXACT_URL:
            # Identical URL and content: a re-observation, nothing to do.
            return IngestOutcome(
                disposition=IngestDisposition.DUPLICATE,
                entry_title=entry.title,
                canonical_url=canonical.canonical_url,
                candidate=candidate,
                canonicalization=canonical,
                detail=candidate.reason,
            )

        if (candidate.should_supersede
                and candidate.existing_article_id is not None):
            # Same URL, materially changed content: a new version. The original
            # is never modified (§5.3.1, criterion 9).
            return await self._capture_as_revision(
                source, entry, canonical, candidate, digest)

        if candidate.is_duplicate and not candidate.should_discard:
            # Near-duplicate or syndicated: retained, and the relationship is
            # recorded so repeated wire reports are not counted as independent
            # corroboration (§7.5, I9).
            outcome = await self._capture(
                source, entry, canonical, candidate, digest)
            if outcome.article_id and candidate.existing_article_id:
                await self._record_relationship(
                    outcome.article_id, candidate.existing_article_id,
                    candidate)
            return IngestOutcome(
                disposition=IngestDisposition.ASSOCIATED,
                entry_title=entry.title,
                article_id=outcome.article_id,
                canonical_url=canonical.canonical_url,
                candidate=candidate,
                canonicalization=canonical,
                outbox_message_id=outcome.outbox_message_id,
                detail=candidate.reason,
            )

        return await self._capture(source, entry, canonical, candidate, digest)

    async def _capture(
        self,
        source: Source,
        entry: NormalizedEntry,
        canonical: CanonicalizationResult,
        candidate: DuplicateCandidate,
        digest: str,
    ) -> IngestOutcome:
        article = Article(
            source_id=source.id,
            canonical_url=canonical.canonical_url,
            original_url=entry.link,
            title=entry.title,
            author=entry.author,
            # A missing publication timestamp stays None. Inventing one would
            # fabricate an observation time (§7.2, ADR-012).
            published_at=entry.published_at,
            source_updated_at=entry.updated_at,
            language=entry.language or "en",
            content_hash=digest,
            normalized_text=entry.content or entry.summary,
            extraction_status=ExtractionStatus.SUCCEEDED,
            metadata_json={
                "guid": entry.guid,
                "canonicalization": canonical.to_dict(),
                "categories": entry.categories,
            },
        )
        return await self._store_article(article, source, entry, canonical,
                                         candidate)

    async def _capture_as_revision(
        self,
        source: Source,
        entry: NormalizedEntry,
        canonical: CanonicalizationResult,
        candidate: DuplicateCandidate,
        digest: str,
    ) -> IngestOutcome:
        revised = Article(
            source_id=source.id,
            canonical_url=canonical.canonical_url,
            original_url=entry.link,
            title=entry.title,
            author=entry.author,
            published_at=entry.published_at,
            source_updated_at=entry.updated_at,
            language=entry.language or "en",
            content_hash=digest,
            normalized_text=entry.content or entry.summary,
            extraction_status=ExtractionStatus.SUCCEEDED,
            metadata_json={
                "guid": entry.guid,
                "canonicalization": canonical.to_dict(),
                "revision_of": candidate.existing_article_id,
            },
        )
        try:
            article_id = await self.store.supersede_article(
                candidate.existing_article_id, revised)
        except Exception as exc:
            return IngestOutcome(
                disposition=IngestDisposition.DUPLICATE,
                entry_title=entry.title,
                canonical_url=canonical.canonical_url,
                candidate=candidate,
                detail=f"could not record revision: {exc}",
            )
        return IngestOutcome(
            disposition=IngestDisposition.SUPERSEDED,
            entry_title=entry.title,
            article_id=article_id,
            canonical_url=canonical.canonical_url,
            candidate=candidate,
            canonicalization=canonical,
            detail="material revision captured as a new version",
        )

    async def _store_article(
        self,
        article: Article,
        source: Source,
        entry: NormalizedEntry,
        canonical: CanonicalizationResult,
        candidate: DuplicateCandidate,
    ) -> IngestOutcome:
        """Persist the article and schedule downstream work atomically."""
        # Idempotency: a retry of the same article version must not create a
        # second active record (criterion 7).
        key = f"article-capture:{article.source_id}:{article.content_hash}"
        prior = self.store.check_idempotency(key)
        if prior:
            return IngestOutcome(
                disposition=IngestDisposition.DUPLICATE,
                entry_title=entry.title,
                article_id=prior,
                canonical_url=canonical.canonical_url,
                candidate=candidate,
                detail="already captured (idempotency key)",
            )

        conn = self.store.db.connect()
        try:
            with self.store.db.transaction():
                # The article insert is issued inside this transaction so the
                # outbox row and the article commit together.
                conn.execute(
                    """INSERT INTO articles
                       (id, source_id, canonical_url, original_url, title, author,
                        published_at, source_updated_at, first_seen_at, fetched_at,
                        language, content_hash, normalized_text,
                        raw_content_reference, extraction_status,
                        publication_version, supersedes_article_id, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        article.id, article.source_id, article.canonical_url,
                        article.original_url, article.title, article.author,
                        _iso(article.published_at), _iso(article.source_updated_at),
                        _iso(article.first_seen_at), _iso(article.fetched_at),
                        article.language, article.content_hash,
                        article.normalized_text, article.raw_content_reference,
                        article.extraction_status.value,
                        article.publication_version,
                        article.supersedes_article_id,
                        _json(article.metadata_json),
                    ),
                )
                self.store._emit_event(
                    conn, "ArticleCaptured", "article", article.id,
                    {
                        "source_id": article.source_id,
                        "canonical_url": article.canonical_url,
                        "content_hash": article.content_hash,
                        "published_at": _iso(article.published_at),
                    },
                    occurred_at=article.published_at,
                )
                outbox_id = None
                if self.policy.schedule_knowledge_processing:
                    outbox_id = self.store.enqueue_outbox_in(
                        conn, "compile-article",
                        {"article_id": article.id, "source_id": article.source_id},
                    )
        except Exception as exc:
            # A constraint violation here means the article was already
            # captured concurrently; treat it as a duplicate, not a failure.
            return IngestOutcome(
                disposition=IngestDisposition.DUPLICATE,
                entry_title=entry.title,
                canonical_url=canonical.canonical_url,
                candidate=candidate,
                detail=f"capture refused: {exc}",
            )

        self.store.record_idempotency(key, "article-capture", article.id)
        await self.store.record_source_attempt(source.id, success=True)

        return IngestOutcome(
            disposition=IngestDisposition.CAPTURED,
            entry_title=entry.title,
            article_id=article.id,
            canonical_url=canonical.canonical_url,
            candidate=candidate,
            canonicalization=canonical,
            outbox_message_id=outbox_id,
        )

    async def _record_relationship(
        self, new_article_id: str, existing_article_id: str,
        candidate: DuplicateCandidate,
    ) -> None:
        """Record a deduplication relationship as a durable event.

        The relationship is a candidate with confidence, not an irreversible
        merge (§7.5).
        """
        conn = self.store.db.connect()
        with self.store.db.transaction():
            self.store._emit_event(
                conn, "DuplicateCandidateDetected", "article", new_article_id,
                {
                    "kind": candidate.kind.value,
                    "existing_article_id": existing_article_id,
                    "confidence": candidate.confidence,
                    "similarity": candidate.similarity,
                    "reason": candidate.reason,
                },
            )

    # ── helpers ──────────────────────────────────────────

    async def _existing_articles(self) -> List[ExistingArticle]:
        articles = await self.store.list_articles()
        return [
            ExistingArticle(
                id=a.id,
                canonical_url=a.canonical_url,
                content_hash=a.content_hash,
                normalized_text=a.normalized_text,
                source_id=a.source_id,
            )
            for a in articles
        ]

    async def pending_work(self) -> List[Dict[str, Any]]:
        """Outbox rows awaiting the knowledge compiler (recovery source)."""
        return await self.store.pending_outbox()


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _json(value: Dict[str, Any]) -> str:
    import json
    return json.dumps(value, default=str)
