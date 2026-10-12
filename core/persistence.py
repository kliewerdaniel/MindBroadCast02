"""SQLite persistence layer for MindBroadcast.

Raw sqlite3, no ORM (per prior PERSISTENCE.md decision).

Design rules (spec §6.1):
- Foreign keys enforced on EVERY connection.
- WAL mode for on-disk databases (concurrent readers + one writer).
- Explicit transaction boundaries; never hold a write transaction open across
  network or model operations.
- Bounded busy timeout.
- Parameterized SQL always.
- Append-only core tables: no UPDATE or DELETE on historical records.

Bugs fixed relative to the pre-Phase-1 implementation (see
docs/PHASE_0_AUDIT.md):

1. In-memory databases were shared through a module-level dict, so every
   WorldStore instance saw every other instance's data. Each store now owns a
   private connection.
2. Database.initialize() referenced `self.db.db_path` inside a Database method
   where `self` is the Database — an AttributeError on every non-memory path,
   and it never closed the connection it opened.
3. _close_conn had the same self.db.db_path bug plus an infinite-recursion path.
"""

import sqlite3
import json
import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from .models import (
    Source, OwnershipGroup, Article, Entity, Event, Story, Claim, Evidence,
    ClaimEvidence, Correction, WorldSnapshot,
    SourceType, EntityType, EntityStatus, EventStatus, StoryStatus,
    ClaimType, ClaimStatus, CorrectionType, EvidenceRelationship,
    ExtractionStatus,
    StoryFilter, ClaimFilter, SnapshotDiff,
    claim_status_from_str,
)

SCHEMA_VERSION = 2


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_str(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _str_to_dt(
    s: Optional[str], default_now: bool = False
) -> Optional[datetime]:
    """Parse an ISO timestamp. Naive values are assumed UTC.

    With default_now=True the return is never None, for fields the domain
    model declares non-optional.
    """
    if not s:
        return _utcnow() if default_now else None
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return _utcnow() if default_now else None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _require_dt(s: Optional[str]) -> datetime:
    """Non-optional variant for required timestamp fields."""
    value = _str_to_dt(s, default_now=True)
    assert value is not None  # default_now guarantees this
    return value


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _json_loads(s: Optional[str], default: Any = None) -> Any:
    if not s:
        return default if default is not None else []
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def content_hash(text: str) -> str:
    """Stable content fingerprint for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PersistenceError(Exception):
    """Persistence layer error."""


# ── Schema ───────────────────────────────────────────────────────────

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ownership_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    canonical_domain TEXT,
    source_type TEXT NOT NULL DEFAULT 'rss',
    endpoint TEXT NOT NULL DEFAULT '',
    publisher_identity TEXT,
    ownership_group_id TEXT,
    trust_profile_id TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    poll_interval_seconds INTEGER NOT NULL DEFAULT 300,
    last_attempt_at TIMESTAMP,
    last_success_at TIMESTAMP,
    failure_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    FOREIGN KEY (ownership_group_id) REFERENCES ownership_groups(id)
);

CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    original_url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    author TEXT,
    published_at TIMESTAMP,
    source_updated_at TIMESTAMP,
    first_seen_at TIMESTAMP NOT NULL,
    fetched_at TIMESTAMP NOT NULL,
    language TEXT NOT NULL DEFAULT 'en',
    content_hash TEXT NOT NULL UNIQUE,
    normalized_text TEXT NOT NULL DEFAULT '',
    raw_content_reference TEXT,
    extraction_status TEXT NOT NULL DEFAULT 'pending',
    publication_version INTEGER NOT NULL DEFAULT 1,
    supersedes_article_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (source_id) REFERENCES sources(id),
    FOREIGN KEY (supersedes_article_id) REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT 'concept',
    description TEXT NOT NULL DEFAULT '',
    aliases TEXT NOT NULL DEFAULT '[]',
    external_identifiers TEXT NOT NULL DEFAULT '{}',
    canonical_uri TEXT,
    first_observed_at TIMESTAMP NOT NULL,
    last_observed_at TIMESTAMP NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(canonical_name, entity_type)
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    canonical_title TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'emerging',
    occurred_at_start TIMESTAMP,
    occurred_at_end TIMESTAMP,
    first_observed_at TIMESTAMP NOT NULL,
    last_material_update_at TIMESTAMP NOT NULL,
    parent_event_id TEXT,
    supersedes_event_id TEXT,
    confidence_state TEXT NOT NULL DEFAULT 'unassessed',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (parent_event_id) REFERENCES events(id),
    FOREIGN KEY (supersedes_event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS stories (
    id TEXT PRIMARY KEY,
    canonical_title TEXT NOT NULL DEFAULT '',
    editorial_summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMP NOT NULL,
    last_material_update_at TIMESTAMP NOT NULL,
    last_broadcast_at TIMESTAMP,
    last_substantial_coverage_at TIMESTAMP,
    audience_relevance_state TEXT NOT NULL DEFAULT 'neutral',
    unresolved_question_count INTEGER NOT NULL DEFAULT 0,
    entity_ids TEXT NOT NULL DEFAULT '[]',
    claim_ids TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.0,
    corrected_by TEXT
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    canonical_proposition TEXT NOT NULL,
    claim_type TEXT NOT NULL DEFAULT 'observational',
    epistemic_status TEXT NOT NULL DEFAULT 'unassessed',
    normalized_subject_id TEXT,
    normalized_predicate TEXT,
    normalized_object TEXT,
    first_observed_at TIMESTAMP NOT NULL,
    last_evaluated_at TIMESTAMP,
    valid_time_start TIMESTAMP,
    valid_time_end TIMESTAMP,
    supersedes_claim_id TEXT,
    created_by_component TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    corrected_by TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (supersedes_claim_id) REFERENCES claims(id)
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    evidence_type TEXT NOT NULL DEFAULT 'passage',
    article_id TEXT,
    source_id TEXT NOT NULL DEFAULT '',
    content_reference TEXT NOT NULL DEFAULT '',
    excerpt TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    observed_at TIMESTAMP NOT NULL,
    source_published_at TIMESTAMP,
    extraction_method TEXT NOT NULL DEFAULT '',
    extraction_model TEXT,
    extraction_version TEXT NOT NULL DEFAULT '',
    provenance_status TEXT NOT NULL DEFAULT 'unverified',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (article_id) REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    assessment TEXT,
    assessment_method TEXT NOT NULL DEFAULT '',
    assessed_at TIMESTAMP,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (claim_id) REFERENCES claims(id),
    FOREIGN KEY (evidence_id) REFERENCES evidence(id),
    UNIQUE(claim_id, evidence_id, relationship_type)
);

CREATE TABLE IF NOT EXISTS corrections (
    id TEXT PRIMARY KEY,
    correction_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    originating_source_id TEXT,
    original_value_reference TEXT NOT NULL DEFAULT '',
    corrected_value_reference TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    detected_at TIMESTAMP NOT NULL,
    effective_at TIMESTAMP,
    review_status TEXT NOT NULL DEFAULT 'pending',
    supersedes_correction_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (supersedes_correction_id) REFERENCES corrections(id)
);

CREATE TABLE IF NOT EXISTS world_snapshots (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP NOT NULL,
    knowledge_watermark TEXT NOT NULL DEFAULT '',
    schema_version INTEGER NOT NULL DEFAULT 2,
    snapshot_type TEXT NOT NULL DEFAULT 'full',
    parent_snapshot_id TEXT,
    content_hash TEXT NOT NULL DEFAULT '',
    included_record_manifest TEXT NOT NULL DEFAULT '{}',
    unresolved_issue_count INTEGER NOT NULL DEFAULT 0,
    story_count INTEGER NOT NULL DEFAULT 0,
    claim_count INTEGER NOT NULL DEFAULT 0,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    entity_count INTEGER NOT NULL DEFAULT 0,
    article_count INTEGER NOT NULL DEFAULT 0,
    state_reference TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (parent_snapshot_id) REFERENCES world_snapshots(id)
);

CREATE TABLE IF NOT EXISTS domain_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    sequence_number INTEGER NOT NULL,
    occurred_at TIMESTAMP NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    causation_id TEXT,
    correlation_id TEXT,
    actor_type TEXT NOT NULL DEFAULT 'system',
    actor_id TEXT NOT NULL DEFAULT 'system',
    schema_version INTEGER NOT NULL DEFAULT 1,
    payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS outbox_messages (
    id TEXT PRIMARY KEY,
    message_type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL,
    published_at TIMESTAMP,
    delivery_attempts INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL,
    result_id TEXT,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_hash ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_supersedes ON articles(supersedes_article_id);
CREATE INDEX IF NOT EXISTS idx_evidence_article ON evidence(article_id);
CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence(source_id);
CREATE INDEX IF NOT EXISTS idx_claim_evidence_claim ON claim_evidence(claim_id);
CREATE INDEX IF NOT EXISTS idx_claim_evidence_evidence ON claim_evidence(evidence_id);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(epistemic_status);
CREATE INDEX IF NOT EXISTS idx_claims_type ON claims(claim_type);
CREATE INDEX IF NOT EXISTS idx_stories_status ON stories(status);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(canonical_name);
CREATE INDEX IF NOT EXISTS idx_corrections_target ON corrections(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_domain_events_aggregate ON domain_events(aggregate_type, aggregate_id);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_messages(published_at);

-- Append-only enforcement (spec §3.3, §6.3).
--
-- The domain event log and the historical record tables must never be mutated
-- or deleted. Enforcing this at the database level means the invariant holds
-- even if a future write path forgets the convention in application code.
--
-- SQLite's RAISE(ABORT, ...) aborts the offending statement with an error,
-- which is what makes the invariant enforceable rather than conventional.
CREATE TRIGGER IF NOT EXISTS trg_domain_events_no_update
BEFORE UPDATE ON domain_events
BEGIN
    SELECT RAISE(ABORT, 'domain_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_domain_events_no_delete
BEFORE DELETE ON domain_events
BEGIN
    SELECT RAISE(ABORT, 'domain_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_articles_no_update
BEFORE UPDATE ON articles
BEGIN
    SELECT RAISE(ABORT, 'articles is append-only: supersede instead');
END;

CREATE TRIGGER IF NOT EXISTS trg_articles_no_delete
BEFORE DELETE ON articles
BEGIN
    SELECT RAISE(ABORT, 'articles is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_claims_no_update
BEFORE UPDATE ON claims
BEGIN
    SELECT RAISE(ABORT, 'claims is append-only: supersede instead');
END;

CREATE TRIGGER IF NOT EXISTS trg_claims_no_delete
BEFORE DELETE ON claims
BEGIN
    SELECT RAISE(ABORT, 'claims is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_evidence_no_update
BEFORE UPDATE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_evidence_no_delete
BEFORE DELETE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_corrections_no_update
BEFORE UPDATE ON corrections
BEGIN
    SELECT RAISE(ABORT, 'corrections is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_corrections_no_delete
BEFORE DELETE ON corrections
BEGIN
    SELECT RAISE(ABORT, 'corrections is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_world_snapshots_no_update
BEFORE UPDATE ON world_snapshots
BEGIN
    SELECT RAISE(ABORT, 'world_snapshots is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_world_snapshots_no_delete
BEFORE DELETE ON world_snapshots
BEGIN
    SELECT RAISE(ABORT, 'world_snapshots is append-only');
END;
"""


class Database:
    """SQLite connection manager.

    Each instance owns exactly one connection. In-memory databases are private
    to the instance — no module-level sharing.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        self._sequence = 0
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # ── connection lifecycle ─────────────────────────────

    def connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                conn = sqlite3.connect(
                    self.db_path,
                    check_same_thread=False,
                    timeout=5.0,
                )
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=5000")
                if self.db_path != ":memory:":
                    conn.execute("PRAGMA journal_mode=WAL")
                conn.row_factory = sqlite3.Row
                self._conn = conn
            return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> "Database":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ── schema ───────────────────────────────────────────

    def initialize(self) -> int:
        conn = self.connect()
        with self._lock:
            conn.executescript(CREATE_TABLES_SQL)
            row = conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
                version = SCHEMA_VERSION
            else:
                version = row[0]
            conn.commit()
            return version

    def get_schema_version(self) -> int:
        conn = self.connect()
        row = conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else 0

    def next_sequence(self) -> int:
        """Monotonic per-database sequence number for domain events."""
        with self._lock:
            self._sequence += 1
            return self._sequence

    # ── transaction helper ───────────────────────────────

    def transaction(self):
        """Context manager giving an explicit transaction boundary."""
        return _Transaction(self)


class _Transaction:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self) -> sqlite3.Connection:
        self.conn = self.db.connect()
        self.conn.execute("BEGIN")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        return False


class WorldStore:
    """Append-only world state store.

    Every write path: read context → write records in one short transaction →
    emit a domain event → commit. Model calls and network I/O never happen
    inside a transaction.
    """

    def __init__(self, db_path: str = "data/world.db"):
        self.db = Database(db_path)
        self.db.initialize()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "WorldStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ── internal helpers ─────────────────────────────────

    def _emit_event(
        self,
        conn: sqlite3.Connection,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Dict[str, Any],
        occurred_at: Optional[datetime] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> str:
        """Insert a domain event inside the caller's transaction."""
        from uuid import uuid4
        event_id = str(uuid4())
        now = _utcnow()
        conn.execute(
            """INSERT INTO domain_events
               (event_id, event_type, aggregate_type, aggregate_id,
                sequence_number, occurred_at, recorded_at, causation_id,
                correlation_id, actor_type, actor_id, schema_version, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id, event_type, aggregate_type, aggregate_id,
                self.db.next_sequence(),
                _dt_to_str(occurred_at or now), _dt_to_str(now),
                causation_id, correlation_id, "system", "system", 1,
                _json_dumps(payload),
            ),
        )
        return event_id

    def check_idempotency(self, key: str) -> Optional[str]:
        """Return a prior result_id for this key, or None."""
        conn = self.db.connect()
        row = conn.execute(
            "SELECT result_id FROM idempotency_keys WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def record_idempotency(
        self, key: str, operation_type: str, result_id: str
    ) -> None:
        conn = self.db.connect()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO idempotency_keys
                   (key, operation_type, result_id, created_at)
                   VALUES (?, ?, ?, ?)""",
                (key, operation_type, result_id, _dt_to_str(_utcnow())),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            pass

    # ── ownership groups ─────────────────────────────────

    async def add_ownership_group(self, group: OwnershipGroup) -> str:
        conn = self.db.connect()
        try:
            with self.db.transaction():
                conn.execute(
                    """INSERT INTO ownership_groups (id, name, created_at)
                       VALUES (?, ?, ?)""",
                    (group.id, group.name, _dt_to_str(group.created_at)),
                )
                self._emit_event(
                    conn, "OwnershipGroupRegistered", "ownership_group",
                    group.id, {"name": group.name},
                )
            return group.id
        except sqlite3.IntegrityError as e:
            raise PersistenceError(f"Failed to add ownership group: {e}")

    async def get_ownership_group(self, group_id: str) -> Optional[OwnershipGroup]:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT id, name, created_at FROM ownership_groups WHERE id = ?",
            (group_id,),
        ).fetchone()
        if row is None:
            return None
        return OwnershipGroup(
            id=row[0], name=row[1],
            created_at=_require_dt(row[2]),
        )

    # ── sources ──────────────────────────────────────────

    async def add_source(self, source: Source) -> str:
        conn = self.db.connect()
        try:
            with self.db.transaction():
                conn.execute(
                    """INSERT INTO sources
                       (id, name, canonical_domain, source_type, endpoint,
                        publisher_identity, ownership_group_id, trust_profile_id,
                        enabled, poll_interval_seconds, last_attempt_at,
                        last_success_at, failure_count, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source.id, source.name, source.canonical_domain,
                        source.source_type.value, source.endpoint,
                        source.publisher_identity, source.ownership_group_id,
                        source.trust_profile_id, int(source.enabled),
                        source.poll_interval_seconds,
                        _dt_to_str(source.last_attempt_at),
                        _dt_to_str(source.last_success_at),
                        source.failure_count,
                        _dt_to_str(source.created_at), _dt_to_str(source.updated_at),
                    ),
                )
                self._emit_event(
                    conn, "SourceRegistered", "source", source.id,
                    {"name": source.name, "endpoint": source.endpoint},
                )
            return source.id
        except sqlite3.IntegrityError as e:
            raise PersistenceError(f"Failed to add source: {e}")

    async def get_source(self, source_id: str) -> Optional[Source]:
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_source(row)

    async def list_sources(self, enabled_only: bool = False) -> List[Source]:
        conn = self.db.connect()
        query = "SELECT * FROM sources"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY name"
        return [self._row_to_source(r) for r in conn.execute(query).fetchall()]

    async def record_source_attempt(self, source_id: str, success: bool) -> None:
        """Record a fetch attempt. Operational state, not a historical rewrite."""
        conn = self.db.connect()
        now = _dt_to_str(_utcnow())
        if success:
            conn.execute(
                """UPDATE sources SET last_attempt_at = ?, last_success_at = ?,
                   failure_count = 0, updated_at = ? WHERE id = ?""",
                (now, now, now, source_id),
            )
        else:
            conn.execute(
                """UPDATE sources SET last_attempt_at = ?,
                   failure_count = failure_count + 1, updated_at = ?
                   WHERE id = ?""",
                (now, now, source_id),
            )
        conn.commit()

    def _row_to_source(self, row: sqlite3.Row) -> Source:
        return Source(
            id=row["id"], name=row["name"],
            canonical_domain=row["canonical_domain"],
            source_type=SourceType(row["source_type"]),
            endpoint=row["endpoint"],
            publisher_identity=row["publisher_identity"],
            ownership_group_id=row["ownership_group_id"],
            trust_profile_id=row["trust_profile_id"],
            enabled=bool(row["enabled"]),
            poll_interval_seconds=row["poll_interval_seconds"],
            last_attempt_at=_str_to_dt(row["last_attempt_at"]),
            last_success_at=_str_to_dt(row["last_success_at"]),
            failure_count=row["failure_count"],
            created_at=_require_dt(row["created_at"]),
            updated_at=_require_dt(row["updated_at"]),
        )

    # ── articles ─────────────────────────────────────────

    async def add_article(self, article: Article) -> str:
        """Capture an article. Idempotent on content_hash."""
        conn = self.db.connect()
        try:
            with self.db.transaction():
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
                        _dt_to_str(article.published_at),
                        _dt_to_str(article.source_updated_at),
                        _dt_to_str(article.first_seen_at),
                        _dt_to_str(article.fetched_at),
                        article.language, article.content_hash,
                        article.normalized_text, article.raw_content_reference,
                        article.extraction_status.value,
                        article.publication_version,
                        article.supersedes_article_id,
                        _json_dumps(article.metadata_json),
                    ),
                )
                self._emit_event(
                    conn, "ArticleCaptured", "article", article.id,
                    {
                        "source_id": article.source_id,
                        "canonical_url": article.canonical_url,
                        "publication_version": article.publication_version,
                        "supersedes": article.supersedes_article_id,
                    },
                    occurred_at=article.published_at,
                )
            return article.id
        except sqlite3.IntegrityError as e:
            raise PersistenceError(f"Failed to add article: {e}")

    async def get_article(self, article_id: str) -> Optional[Article]:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        return self._row_to_article(row) if row else None

    async def get_article_by_hash(self, hash_value: str) -> Optional[Article]:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM articles WHERE content_hash = ? LIMIT 1", (hash_value,)
        ).fetchone()
        return self._row_to_article(row) if row else None

    async def get_article_by_url(self, canonical_url: str) -> Optional[Article]:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM articles WHERE canonical_url = ? LIMIT 1",
            (canonical_url,),
        ).fetchone()
        return self._row_to_article(row) if row else None

    async def list_articles(
        self,
        source_id: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> List[Article]:
        conn = self.db.connect()
        query = "SELECT * FROM articles WHERE 1=1"
        params: List[Any] = []
        if source_id:
            query += " AND source_id = ?"
            params.append(source_id)
        if since:
            query += " AND published_at >= ?"
            params.append(_dt_to_str(since))
        if until:
            query += " AND published_at <= ?"
            params.append(_dt_to_str(until))
        query += " ORDER BY published_at DESC"
        if limit:
            query += f" LIMIT {int(limit)}"
        return [self._row_to_article(r) for r in conn.execute(query, params).fetchall()]

    async def count_articles(self) -> int:
        conn = self.db.connect()
        return conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    async def supersede_article(
        self, original_id: str, revised: Article
    ) -> str:
        """Capture a materially revised version of an article.

        The original is never modified or deleted — the new version links back
        through supersedes_article_id (§5.3.1).
        """
        original = await self.get_article(original_id)
        if original is None:
            raise PersistenceError(f"Article {original_id} not found")
        revised.supersedes_article_id = original_id
        revised.publication_version = original.publication_version + 1
        revised.source_id = original.source_id
        revised.canonical_url = original.canonical_url
        revised.first_seen_at = original.first_seen_at
        new_id = await self.add_article(revised)

        conn = self.db.connect()
        with self.db.transaction():
            self._emit_event(
                conn, "ArticleVersionDetected", "article", new_id,
                {
                    "supersedes": original_id,
                    "publication_version": revised.publication_version,
                },
                occurred_at=revised.published_at,
                correlation_id=original_id,
            )
        return new_id

    async def get_article_versions(self, article_id: str) -> List[Article]:
        """All versions in a chain, oldest first.

        Walks backwards from the given version to the root, then orders by
        publication_version, so the call works from either end of the chain.
        """
        conn = self.db.connect()
        rows = conn.execute(
            """WITH RECURSIVE chain(id) AS (
                   SELECT ?
                   UNION
                   SELECT a.supersedes_article_id FROM articles a JOIN chain c
                     ON a.id = c.id
                   WHERE a.supersedes_article_id IS NOT NULL
               )
               SELECT * FROM articles WHERE id IN (SELECT id FROM chain)
               ORDER BY publication_version ASC""",
            (article_id,),
        ).fetchall()
        return [self._row_to_article(r) for r in rows]

    def _row_to_article(self, row: sqlite3.Row) -> Article:
        return Article(
            id=row["id"], source_id=row["source_id"],
            canonical_url=row["canonical_url"],
            original_url=row["original_url"], title=row["title"],
            author=row["author"],
            published_at=_str_to_dt(row["published_at"]),
            source_updated_at=_str_to_dt(row["source_updated_at"]),
            first_seen_at=_require_dt(row["first_seen_at"]),
            fetched_at=_require_dt(row["fetched_at"]),
            language=row["language"], content_hash=row["content_hash"],
            normalized_text=row["normalized_text"],
            raw_content_reference=row["raw_content_reference"],
            extraction_status=ExtractionStatus(row["extraction_status"]),
            publication_version=row["publication_version"],
            supersedes_article_id=row["supersedes_article_id"],
            metadata_json=_json_loads(row["metadata_json"], {}),
        )

    # ── entities ─────────────────────────────────────────

    async def add_entity(self, entity: Entity) -> str:
        conn = self.db.connect()
        try:
            with self.db.transaction():
                existing = conn.execute(
                    """SELECT id FROM entities
                       WHERE canonical_name = ? AND entity_type = ?""",
                    (entity.canonical_name, entity.entity_type.value),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """INSERT INTO entities
                           (id, canonical_name, entity_type, description, aliases,
                            external_identifiers, canonical_uri, first_observed_at,
                            last_observed_at, status, metadata_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            entity.id, entity.canonical_name,
                            entity.entity_type.value, entity.description,
                            _json_dumps(entity.aliases),
                            _json_dumps(entity.external_identifiers),
                            entity.canonical_uri,
                            _dt_to_str(entity.first_observed_at),
                            _dt_to_str(entity.last_observed_at),
                            entity.status.value,
                            _json_dumps(entity.metadata_json),
                        ),
                    )
                    result_id = entity.id
                    event_type = "EntityResolved"
                else:
                    # Same canonical identity observed again: update the
                    # observation window only. This is operational state, not
                    # a historical rewrite.
                    result_id = existing["id"]
                    event_type = "EntityReobserved"
                    conn.execute(
                        """UPDATE entities
                           SET last_observed_at = ?
                           WHERE id = ?""",
                        (_dt_to_str(entity.last_observed_at), result_id),
                    )
                self._emit_event(
                    conn, event_type, "entity", result_id,
                    {"canonical_name": entity.canonical_name,
                     "entity_type": entity.entity_type.value},
                )
            return result_id
        except sqlite3.IntegrityError as e:
            raise PersistenceError(f"Failed to add entity: {e}")

    async def get_entity(self, entity_id: str) -> Optional[Entity]:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        return self._row_to_entity(row) if row else None

    async def find_entity_by_name(
        self, name: str, entity_type: Optional[EntityType] = None
    ) -> Optional[Entity]:
        conn = self.db.connect()
        if entity_type:
            row = conn.execute(
                """SELECT * FROM entities
                   WHERE canonical_name = ? AND entity_type = ? AND status = 'active'""",
                (name, entity_type.value),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM entities WHERE canonical_name = ? AND status = 'active'",
                (name,),
            ).fetchone()
        return self._row_to_entity(row) if row else None

    async def list_entities(
        self, entity_type: Optional[EntityType] = None
    ) -> List[Entity]:
        conn = self.db.connect()
        query = "SELECT * FROM entities"
        params: List[Any] = []
        if entity_type:
            query += " WHERE entity_type = ?"
            params.append(entity_type.value)
        query += " ORDER BY canonical_name"
        return [self._row_to_entity(r) for r in conn.execute(query, params).fetchall()]

    async def count_entities(self) -> int:
        conn = self.db.connect()
        return conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

    async def merge_entities(self, survivor_id: str, absorbed_id: str) -> str:
        """Merge two entities. An explicit, recorded, reversible operation (§5.4).

        Embedding similarity may propose a merge; it never authorizes one.
        """
        conn = self.db.connect()
        with self.db.transaction():
            survivor = conn.execute(
                "SELECT id FROM entities WHERE id = ?", (survivor_id,)
            ).fetchone()
            absorbed = conn.execute(
                "SELECT id FROM entities WHERE id = ?", (absorbed_id,)
            ).fetchone()
            if survivor is None or absorbed is None:
                raise PersistenceError("Both entities must exist to merge")
            conn.execute(
                "UPDATE entities SET status = 'merged' WHERE id = ?", (absorbed_id,)
            )
            self._emit_event(
                conn, "EntityMerged", "entity", absorbed_id,
                {"merged_into": survivor_id},
            )
        return survivor_id

    def _row_to_entity(self, row: sqlite3.Row) -> Entity:
        return Entity(
            id=row["id"], canonical_name=row["canonical_name"],
            entity_type=EntityType(row["entity_type"]),
            description=row["description"],
            aliases=_json_loads(row["aliases"], []),
            external_identifiers=_json_loads(row["external_identifiers"], {}),
            canonical_uri=row["canonical_uri"],
            first_observed_at=_require_dt(row["first_observed_at"]),
            last_observed_at=_require_dt(row["last_observed_at"]),
            status=EntityStatus(row["status"]),
            metadata_json=_json_loads(row["metadata_json"], {}),
        )

    # ── events ───────────────────────────────────────────

    async def add_event(self, event: Event) -> str:
        conn = self.db.connect()
        try:
            with self.db.transaction():
                conn.execute(
                    """INSERT INTO events
                       (id, canonical_title, event_type, summary, status,
                        occurred_at_start, occurred_at_end, first_observed_at,
                        last_material_update_at, parent_event_id,
                        supersedes_event_id, confidence_state, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.id, event.canonical_title, event.event_type,
                        event.summary, event.status.value,
                        _dt_to_str(event.occurred_at_start),
                        _dt_to_str(event.occurred_at_end),
                        _dt_to_str(event.first_observed_at),
                        _dt_to_str(event.last_material_update_at),
                        event.parent_event_id, event.supersedes_event_id,
                        event.confidence_state, _json_dumps(event.metadata_json),
                    ),
                )
                self._emit_event(
                    conn, "EventRecorded", "event", event.id,
                    {"title": event.canonical_title, "status": event.status.value},
                    occurred_at=event.occurred_at_start,
                )
            return event.id
        except sqlite3.IntegrityError as e:
            raise PersistenceError(f"Failed to add event: {e}")

    async def get_event(self, event_id: str) -> Optional[Event]:
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    async def list_events(
        self, status: Optional[EventStatus] = None
    ) -> List[Event]:
        conn = self.db.connect()
        query = "SELECT * FROM events"
        params: List[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status.value)
        query += " ORDER BY last_material_update_at DESC"
        return [self._row_to_event(r) for r in conn.execute(query, params).fetchall()]

    async def count_events(self) -> int:
        conn = self.db.connect()
        return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def _row_to_event(self, row: sqlite3.Row) -> Event:
        return Event(
            id=row["id"], canonical_title=row["canonical_title"],
            event_type=row["event_type"], summary=row["summary"],
            status=EventStatus(row["status"]),
            occurred_at_start=_str_to_dt(row["occurred_at_start"]),
            occurred_at_end=_str_to_dt(row["occurred_at_end"]),
            first_observed_at=_require_dt(row["first_observed_at"]),
            last_material_update_at=_str_to_dt(
                _require_dt(row["last_material_update_at"])
            ),
            parent_event_id=row["parent_event_id"],
            supersedes_event_id=row["supersedes_event_id"],
            confidence_state=row["confidence_state"],
            metadata_json=_json_loads(row["metadata_json"], {}),
        )

    # ── stories ──────────────────────────────────────────

    async def add_story(self, story: Story) -> str:
        conn = self.db.connect()
        try:
            with self.db.transaction():
                conn.execute(
                    """INSERT INTO stories
                       (id, canonical_title, editorial_summary, status, created_at,
                        last_material_update_at, last_broadcast_at,
                        last_substantial_coverage_at, audience_relevance_state,
                        unresolved_question_count, entity_ids, claim_ids,
                        confidence, corrected_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        story.id, story.canonical_title, story.editorial_summary,
                        story.status.value, _dt_to_str(story.created_at),
                        _dt_to_str(story.last_material_update_at),
                        _dt_to_str(story.last_broadcast_at),
                        _dt_to_str(story.last_substantial_coverage_at),
                        story.audience_relevance_state,
                        story.unresolved_question_count,
                        _json_dumps(story.entity_ids), _json_dumps(story.claim_ids),
                        story.confidence, story.corrected_by,
                    ),
                )
                self._emit_event(
                    conn, "StoryCreated", "story", story.id,
                    {"title": story.canonical_title},
                )
            return story.id
        except sqlite3.IntegrityError as e:
            raise PersistenceError(f"Failed to add story: {e}")

    async def get_story(self, story_id: str) -> Optional[Story]:
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
        return self._row_to_story(row) if row else None

    async def list_stories(
        self, filter: Optional[StoryFilter] = None
    ) -> List[Story]:
        conn = self.db.connect()
        query = "SELECT * FROM stories WHERE 1=1"
        params: List[Any] = []
        if filter:
            if filter.status:
                query += " AND status = ?"
                params.append(filter.status.value)
            if filter.entity_id:
                query += " AND entity_ids LIKE ?"
                params.append(f'%"{filter.entity_id}"%')
            if filter.since:
                query += " AND created_at >= ?"
                params.append(_dt_to_str(filter.since))
            if filter.until:
                query += " AND created_at <= ?"
                params.append(_dt_to_str(filter.until))
        query += " ORDER BY last_material_update_at DESC"
        return [self._row_to_story(r) for r in conn.execute(query, params).fetchall()]

    async def count_stories(self) -> int:
        conn = self.db.connect()
        return conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]

    async def update_story_status(self, story_id: str, status: StoryStatus) -> str:
        """Transition a story's status by creating a superseding record.

        Append-only semantics: the original story row is never modified.
        """
        original = await self.get_story(story_id)
        if original is None:
            raise PersistenceError(f"Story {story_id} not found")

        successor = Story(
            canonical_title=original.canonical_title,
            editorial_summary=original.editorial_summary,
            status=status,
            last_material_update_at=_utcnow(),
            entity_ids=list(original.entity_ids),
            claim_ids=list(original.claim_ids),
            confidence=original.confidence,
            corrected_by=story_id,
        )
        new_id = await self.add_story(successor)

        conn = self.db.connect()
        with self.db.transaction():
            self._emit_event(
                conn, "StoryStatusChanged", "story", new_id,
                {"from_status": original.status.value, "to_status": status.value},
                correlation_id=story_id, causation_id=story_id,
            )
        return new_id

    def _row_to_story(self, row: sqlite3.Row) -> Story:
        return Story(
            id=row["id"], canonical_title=row["canonical_title"],
            editorial_summary=row["editorial_summary"],
            status=StoryStatus(row["status"]),
            created_at=_require_dt(row["created_at"]),
            last_material_update_at=_str_to_dt(
                _require_dt(row["last_material_update_at"])
            ),
            last_broadcast_at=_str_to_dt(row["last_broadcast_at"]),
            last_substantial_coverage_at=_str_to_dt(
                row["last_substantial_coverage_at"]
            ),
            audience_relevance_state=row["audience_relevance_state"],
            unresolved_question_count=row["unresolved_question_count"],
            entity_ids=_json_loads(row["entity_ids"], []),
            claim_ids=_json_loads(row["claim_ids"], []),
            confidence=row["confidence"] or 0.0,
            corrected_by=row["corrected_by"],
        )

    # ── claims ───────────────────────────────────────────

    async def add_claim(self, claim: Claim) -> str:
        conn = self.db.connect()
        try:
            with self.db.transaction():
                conn.execute(
                    """INSERT INTO claims
                       (id, canonical_proposition, claim_type, epistemic_status,
                        normalized_subject_id, normalized_predicate,
                        normalized_object, first_observed_at, last_evaluated_at,
                        valid_time_start, valid_time_end, supersedes_claim_id,
                        created_by_component, confidence, corrected_by, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        claim.id, claim.canonical_proposition,
                        claim.claim_type.value, claim.epistemic_status.value,
                        claim.normalized_subject_id, claim.normalized_predicate,
                        claim.normalized_object,
                        _dt_to_str(claim.first_observed_at),
                        _dt_to_str(claim.last_evaluated_at),
                        _dt_to_str(claim.valid_time_start),
                        _dt_to_str(claim.valid_time_end),
                        claim.supersedes_claim_id, claim.created_by_component,
                        claim.confidence, claim.corrected_by,
                        _json_dumps(claim.metadata_json),
                    ),
                )
                self._emit_event(
                    conn, "ClaimExtracted", "claim", claim.id,
                    {
                        "claim_type": claim.claim_type.value,
                        "status": claim.epistemic_status.value,
                        "component": claim.created_by_component,
                    },
                    occurred_at=claim.valid_time_start,
                )
            return claim.id
        except sqlite3.IntegrityError as e:
            raise PersistenceError(f"Failed to add claim: {e}")

    async def get_claim(self, claim_id: str) -> Optional[Claim]:
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        return self._row_to_claim(row) if row else None

    async def list_claims(
        self, filter: Optional[ClaimFilter] = None
    ) -> List[Claim]:
        conn = self.db.connect()
        query = "SELECT * FROM claims WHERE 1=1"
        params: List[Any] = []
        if filter:
            if filter.status:
                query += " AND epistemic_status = ?"
                params.append(filter.status.value)
            if filter.claim_type:
                query += " AND claim_type = ?"
                params.append(filter.claim_type.value)
            if filter.since:
                query += " AND first_observed_at >= ?"
                params.append(_dt_to_str(filter.since))
            if filter.until:
                query += " AND first_observed_at <= ?"
                params.append(_dt_to_str(filter.until))
        query += " ORDER BY first_observed_at DESC"
        return [self._row_to_claim(r) for r in conn.execute(query, params).fetchall()]

    async def count_claims(self) -> int:
        conn = self.db.connect()
        return conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]

    async def set_claim_status(
        self, claim_id: str, status: ClaimStatus, reason: str = ""
    ) -> ClaimStatus:
        """Record a claim status transition as a new claim record.

        Append-only: the prior assessment is preserved. A status change always
        emits a ClaimAssessmentChanged event.
        """
        original = await self.get_claim(claim_id)
        if original is None:
            raise PersistenceError(f"Claim {claim_id} not found")

        successor = Claim(
            canonical_proposition=original.canonical_proposition,
            claim_type=original.claim_type,
            epistemic_status=status,
            normalized_subject_id=original.normalized_subject_id,
            normalized_predicate=original.normalized_predicate,
            normalized_object=original.normalized_object,
            valid_time_start=original.valid_time_start,
            valid_time_end=original.valid_time_end,
            supersedes_claim_id=claim_id,
            created_by_component=original.created_by_component,
            confidence=original.confidence,
            corrected_by=claim_id,
        )
        successor.last_evaluated_at = _utcnow()
        new_id = await self.add_claim(successor)

        conn = self.db.connect()
        with self.db.transaction():
            self._emit_event(
                conn, "ClaimAssessmentChanged", "claim", new_id,
                {
                    "from_status": original.epistemic_status.value,
                    "to_status": status.value,
                    "reason": reason,
                },
                correlation_id=claim_id, causation_id=claim_id,
            )
        return status

    def _row_to_claim(self, row: sqlite3.Row) -> Claim:
        return Claim(
            id=row["id"],
            canonical_proposition=row["canonical_proposition"],
            claim_type=ClaimType(row["claim_type"]),
            epistemic_status=claim_status_from_str(row["epistemic_status"]),
            normalized_subject_id=row["normalized_subject_id"],
            normalized_predicate=row["normalized_predicate"],
            normalized_object=row["normalized_object"],
            first_observed_at=_require_dt(row["first_observed_at"]),
            last_evaluated_at=_str_to_dt(row["last_evaluated_at"]),
            valid_time_start=_str_to_dt(row["valid_time_start"]),
            valid_time_end=_str_to_dt(row["valid_time_end"]),
            supersedes_claim_id=row["supersedes_claim_id"],
            created_by_component=row["created_by_component"],
            confidence=row["confidence"] or 0.0,
            corrected_by=row["corrected_by"],
            metadata_json=_json_loads(row["metadata_json"], {}),
        )

    # ── evidence ─────────────────────────────────────────

    async def add_evidence(self, evidence: Evidence) -> str:
        conn = self.db.connect()
        try:
            with self.db.transaction():
                conn.execute(
                    """INSERT INTO evidence
                       (id, evidence_type, article_id, source_id, content_reference,
                        excerpt, content_hash, observed_at, source_published_at,
                        extraction_method, extraction_model, extraction_version,
                        provenance_status, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        evidence.id, evidence.evidence_type, evidence.article_id,
                        evidence.source_id, evidence.content_reference,
                        evidence.excerpt, evidence.content_hash,
                        _dt_to_str(evidence.observed_at),
                        _dt_to_str(evidence.source_published_at),
                        evidence.extraction_method, evidence.extraction_model,
                        evidence.extraction_version, evidence.provenance_status,
                        _json_dumps(evidence.metadata_json),
                    ),
                )
                self._emit_event(
                    conn, "EvidenceLinked", "evidence", evidence.id,
                    {
                        "article_id": evidence.article_id,
                        "source_id": evidence.source_id,
                    },
                    occurred_at=evidence.source_published_at,
                )
            return evidence.id
        except sqlite3.IntegrityError as e:
            raise PersistenceError(f"Failed to add evidence: {e}")

    async def get_evidence(self, evidence_id: str) -> Optional[Evidence]:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM evidence WHERE id = ?", (evidence_id,)
        ).fetchone()
        return self._row_to_evidence(row) if row else None

    async def get_evidence_for_claim(
        self, claim_id: str,
        relationship: Optional[EvidenceRelationship] = None,
    ) -> List[Evidence]:
        """Evidence associated with a claim, optionally filtered by relationship."""
        conn = self.db.connect()
        if relationship:
            rows = conn.execute(
                """SELECT e.* FROM evidence e
                   JOIN claim_evidence ce ON ce.evidence_id = e.id
                   WHERE ce.claim_id = ? AND ce.relationship_type = ?
                   ORDER BY e.observed_at ASC""",
                (claim_id, relationship.value),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT e.* FROM evidence e
                   JOIN claim_evidence ce ON ce.evidence_id = e.id
                   WHERE ce.claim_id = ?
                   ORDER BY e.observed_at ASC""",
                (claim_id,),
            ).fetchall()
        return [self._row_to_evidence(r) for r in rows]

    async def list_evidence(self, source_id: Optional[str] = None) -> List[Evidence]:
        conn = self.db.connect()
        if source_id:
            rows = conn.execute(
                "SELECT * FROM evidence WHERE source_id = ? ORDER BY observed_at DESC",
                (source_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM evidence ORDER BY observed_at DESC"
            ).fetchall()
        return [self._row_to_evidence(r) for r in rows]

    async def count_evidence(self) -> int:
        conn = self.db.connect()
        return conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]

    def _row_to_evidence(self, row: sqlite3.Row) -> Evidence:
        return Evidence(
            id=row["id"], evidence_type=row["evidence_type"],
            article_id=row["article_id"], source_id=row["source_id"],
            content_reference=row["content_reference"], excerpt=row["excerpt"],
            content_hash=row["content_hash"],
            observed_at=_require_dt(row["observed_at"]),
            source_published_at=_str_to_dt(row["source_published_at"]),
            extraction_method=row["extraction_method"],
            extraction_model=row["extraction_model"],
            extraction_version=row["extraction_version"],
            provenance_status=row["provenance_status"],
            metadata_json=_json_loads(row["metadata_json"], {}),
        )

    # ── claim-evidence associations ──────────────────────

    async def link_evidence(
        self,
        claim_id: str,
        evidence_id: str,
        relationship: EvidenceRelationship,
        assessment: Optional[str] = None,
        assessment_method: str = "",
    ) -> str:
        """Associate evidence with a claim under a specific relationship.

        The relationship lives on the association: one passage can support
        claim A while contradicting claim B.
        """
        conn = self.db.connect()
        try:
            with self.db.transaction():
                link = ClaimEvidence(
                    claim_id=claim_id, evidence_id=evidence_id,
                    relationship_type=relationship, assessment=assessment,
                    assessment_method=assessment_method,
                    assessed_at=_utcnow(),
                )
                conn.execute(
                    """INSERT INTO claim_evidence
                       (id, claim_id, evidence_id, relationship_type, assessment,
                        assessment_method, assessed_at, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        link.id, claim_id, evidence_id, relationship.value,
                        assessment, assessment_method,
                        _dt_to_str(link.assessed_at),
                        _json_dumps(link.metadata_json),
                    ),
                )
                self._emit_event(
                    conn, "EvidenceLinked", "claim", claim_id,
                    {
                        "evidence_id": evidence_id,
                        "relationship": relationship.value,
                        "assessment_method": assessment_method,
                    },
                )
            return link.id
        except sqlite3.IntegrityError as e:
            raise PersistenceError(f"Failed to link evidence: {e}")

    async def get_evidence_links(self, claim_id: str) -> List[ClaimEvidence]:
        conn = self.db.connect()
        rows = conn.execute(
            """SELECT * FROM claim_evidence WHERE claim_id = ?
               ORDER BY assessed_at ASC""",
            (claim_id,),
        ).fetchall()
        return [
            ClaimEvidence(
                id=r["id"], claim_id=r["claim_id"], evidence_id=r["evidence_id"],
                relationship_type=EvidenceRelationship(r["relationship_type"]),
                assessment=r["assessment"],
                assessment_method=r["assessment_method"],
                assessed_at=_str_to_dt(r["assessed_at"]),
                metadata_json=_json_loads(r["metadata_json"], {}),
            )
            for r in rows
        ]

    async def count_evidence_links(self) -> int:
        conn = self.db.connect()
        return conn.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0]

    # ── corrections ──────────────────────────────────────

    async def add_correction(self, correction: Correction) -> str:
        """Record a correction as a first-class object.

        Never stored as an Evidence row. The corrected target is never
        modified — the correction supersedes, it does not overwrite.
        """
        conn = self.db.connect()
        try:
            with self.db.transaction():
                conn.execute(
                    """INSERT INTO corrections
                       (id, correction_type, target_type, target_id,
                        originating_source_id, original_value_reference,
                        corrected_value_reference, reason, detected_at,
                        effective_at, review_status, supersedes_correction_id,
                        metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        correction.id, correction.correction_type.value,
                        correction.target_type, correction.target_id,
                        correction.originating_source_id,
                        correction.original_value_reference,
                        correction.corrected_value_reference, correction.reason,
                        _dt_to_str(correction.detected_at),
                        _dt_to_str(correction.effective_at),
                        correction.review_status,
                        correction.supersedes_correction_id,
                        _json_dumps(correction.metadata_json),
                    ),
                )
                self._emit_event(
                    conn, "CorrectionDetected", correction.target_type,
                    correction.target_id,
                    {
                        "correction_type": correction.correction_type.value,
                        "reason": correction.reason,
                    },
                    occurred_at=correction.effective_at,
                )
            return correction.id
        except sqlite3.IntegrityError as e:
            raise PersistenceError(f"Failed to add correction: {e}")

    async def get_correction(self, correction_id: str) -> Optional[Correction]:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM corrections WHERE id = ?", (correction_id,)
        ).fetchone()
        return self._row_to_correction(row) if row else None

    async def get_corrections_for(
        self, target_type: str, target_id: str
    ) -> List[Correction]:
        conn = self.db.connect()
        rows = conn.execute(
            """SELECT * FROM corrections WHERE target_type = ? AND target_id = ?
               ORDER BY detected_at ASC""",
            (target_type, target_id),
        ).fetchall()
        return [self._row_to_correction(r) for r in rows]

    async def list_corrections(
        self, correction_type: Optional[CorrectionType] = None
    ) -> List[Correction]:
        conn = self.db.connect()
        query = "SELECT * FROM corrections"
        params: List[Any] = []
        if correction_type:
            query += " WHERE correction_type = ?"
            params.append(correction_type.value)
        query += " ORDER BY detected_at DESC"
        return [self._row_to_correction(r) for r in conn.execute(query, params).fetchall()]

    async def count_corrections(self) -> int:
        conn = self.db.connect()
        return conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]

    def _row_to_correction(self, row: sqlite3.Row) -> Correction:
        return Correction(
            id=row["id"],
            correction_type=CorrectionType(row["correction_type"]),
            target_type=row["target_type"], target_id=row["target_id"],
            originating_source_id=row["originating_source_id"],
            original_value_reference=row["original_value_reference"],
            corrected_value_reference=row["corrected_value_reference"],
            reason=row["reason"],
            detected_at=_require_dt(row["detected_at"]),
            effective_at=_str_to_dt(row["effective_at"]),
            review_status=row["review_status"],
            supersedes_correction_id=row["supersedes_correction_id"],
            metadata_json=_json_loads(row["metadata_json"], {}),
        )

    # ── snapshots ────────────────────────────────────────

    async def create_snapshot(
        self, artifact_dir: Optional[str] = None
    ) -> WorldSnapshot:
        """Capture world state atomically.

        A snapshot is a versioned representation of what the system had
        observed, processed, and retained — not an aggregation of counts.
        Creation happens inside a single read transaction so incompatible
        states are never combined.
        """
        conn = self.db.connect()
        conn.execute("BEGIN")
        try:
            counts = {
                "articles": conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
                "stories": conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0],
                "claims": conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0],
                "evidence": conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0],
                "entities": conn.execute(
                    "SELECT COUNT(*) FROM entities WHERE status = 'active'"
                ).fetchone()[0],
                "corrections": conn.execute(
                    "SELECT COUNT(*) FROM corrections"
                ).fetchone()[0],
            }

            manifest = {
                "articles": [r[0] for r in conn.execute("SELECT id FROM articles")],
                "stories": [r[0] for r in conn.execute("SELECT id FROM stories")],
                "claims": [r[0] for r in conn.execute("SELECT id FROM claims")],
                "evidence": [r[0] for r in conn.execute("SELECT id FROM evidence")],
                "entities": [
                    r[0] for r in conn.execute(
                        "SELECT id FROM entities WHERE status = 'active'"
                    )
                ],
                "corrections": [r[0] for r in conn.execute("SELECT id FROM corrections")],
            }

            watermark_row = conn.execute(
                "SELECT event_id FROM domain_events ORDER BY sequence_number DESC LIMIT 1"
            ).fetchone()
            watermark = watermark_row[0] if watermark_row else ""

            unresolved_row = conn.execute(
                """SELECT COUNT(*) FROM stories WHERE unresolved_question_count > 0"""
            ).fetchone()

            parent_row = conn.execute(
                "SELECT id FROM world_snapshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

            snapshot = WorldSnapshot(
                created_at=_utcnow(),
                knowledge_watermark=watermark,
                schema_version=SCHEMA_VERSION,
                snapshot_type="full",
                parent_snapshot_id=parent_row[0] if parent_row else None,
                included_record_manifest=manifest,
                unresolved_issue_count=unresolved_row[0] or 0,
                story_count=counts["stories"],
                claim_count=counts["claims"],
                evidence_count=counts["evidence"],
                entity_count=counts["entities"],
                article_count=counts["articles"],
            )
            snapshot.content_hash = content_hash(_json_dumps(manifest))

            state_reference = None
            if artifact_dir:
                state_reference = self._write_snapshot_state(artifact_dir, snapshot)
                snapshot.state_reference = state_reference

            conn.execute(
                """INSERT INTO world_snapshots
                   (id, created_at, knowledge_watermark, schema_version,
                    snapshot_type, parent_snapshot_id, content_hash,
                    included_record_manifest, unresolved_issue_count,
                    story_count, claim_count, evidence_count, entity_count,
                    article_count, state_reference, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.id, _dt_to_str(snapshot.created_at),
                    snapshot.knowledge_watermark, snapshot.schema_version,
                    snapshot.snapshot_type, snapshot.parent_snapshot_id,
                    snapshot.content_hash, _json_dumps(manifest),
                    snapshot.unresolved_issue_count, snapshot.story_count,
                    snapshot.claim_count, snapshot.evidence_count,
                    snapshot.entity_count, snapshot.article_count,
                    state_reference, _json_dumps(snapshot.metadata_json),
                ),
            )
            self._emit_event(
                conn, "WorldSnapshotCreated", "world_snapshot", snapshot.id,
                {
                    "watermark": snapshot.knowledge_watermark,
                    "counts": counts,
                    "content_hash": snapshot.content_hash,
                },
            )
            conn.commit()
            return snapshot
        except Exception:
            conn.rollback()
            raise
        finally:
            pass

    def _write_snapshot_state(
        self, artifact_dir: str, snapshot: WorldSnapshot
    ) -> str:
        """Persist the snapshot's serialized state as an artifact.

        Artifact lifecycle (§6.6): write to temp → hash → move → return the
        reference. The DB record is committed by the caller only after this
        returns.
        """
        directory = Path(artifact_dir)
        directory.mkdir(parents=True, exist_ok=True)
        final = directory / f"snapshot-{snapshot.id}.json"
        tmp = directory / f".snapshot-{snapshot.id}.tmp"
        payload = {
            "snapshot_id": snapshot.id,
            "created_at": _dt_to_str(snapshot.created_at),
            "knowledge_watermark": snapshot.knowledge_watermark,
            "included_record_manifest": snapshot.included_record_manifest,
            "counts": {
                "stories": snapshot.story_count,
                "claims": snapshot.claim_count,
                "evidence": snapshot.evidence_count,
                "entities": snapshot.entity_count,
                "articles": snapshot.article_count,
            },
        }
        tmp.write_text(_json_dumps(payload), encoding="utf-8")
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        tmp.replace(final)
        if digest != snapshot.content_hash and snapshot.content_hash:
            # The manifest hash and the artifact hash are different objects;
            # record the artifact's own hash in metadata rather than failing.
            snapshot.metadata_json["artifact_content_hash"] = digest
        return str(final)

    async def get_snapshot(self, snapshot_id: str) -> Optional[WorldSnapshot]:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM world_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        return self._row_to_snapshot(row) if row else None

    async def list_snapshots(self) -> List[WorldSnapshot]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM world_snapshots ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    async def count_snapshots(self) -> int:
        conn = self.db.connect()
        return conn.execute("SELECT COUNT(*) FROM world_snapshots").fetchone()[0]

    async def compare_snapshots(self, a_id: str, b_id: str) -> SnapshotDiff:
        a = await self.get_snapshot(a_id)
        b = await self.get_snapshot(b_id)
        if a is None or b is None:
            raise PersistenceError("One or both snapshots not found")

        diff = SnapshotDiff(a_id=a_id, b_id=b_id)
        for key in ("stories", "claims", "evidence", "entities", "articles", "corrections"):
            old = set(a.included_record_manifest.get(key, []))
            new = set(b.included_record_manifest.get(key, []))
            added = sorted(new - old)
            if key == "stories":
                diff.new_stories = added
            elif key == "claims":
                diff.new_claims = added
            elif key == "evidence":
                diff.new_evidence = added
            elif key == "entities":
                diff.new_entities = added
            elif key == "articles":
                diff.new_articles = added
            elif key == "corrections":
                diff.new_corrections = added

        diff.human_readable = (
            f"Articles: {a.article_count} → {b.article_count} "
            f"(+{len(diff.new_articles)})\n"
            f"Stories: {a.story_count} → {b.story_count} "
            f"(+{len(diff.new_stories)})\n"
            f"Claims: {a.claim_count} → {b.claim_count} "
            f"(+{len(diff.new_claims)})\n"
            f"Evidence: {a.evidence_count} → {b.evidence_count} "
            f"(+{len(diff.new_evidence)})\n"
            f"Entities: {a.entity_count} → {b.entity_count} "
            f"(+{len(diff.new_entities)})\n"
            f"Corrections: +{len(diff.new_corrections)}"
        )
        return diff

    def _row_to_snapshot(self, row: sqlite3.Row) -> WorldSnapshot:
        return WorldSnapshot(
            id=row["id"],
            created_at=_require_dt(row["created_at"]),
            knowledge_watermark=row["knowledge_watermark"],
            schema_version=row["schema_version"],
            snapshot_type=row["snapshot_type"],
            parent_snapshot_id=row["parent_snapshot_id"],
            content_hash=row["content_hash"],
            included_record_manifest=_json_loads(row["included_record_manifest"], {}),
            unresolved_issue_count=row["unresolved_issue_count"],
            story_count=row["story_count"],
            claim_count=row["claim_count"],
            evidence_count=row["evidence_count"],
            entity_count=row["entity_count"],
            article_count=row["article_count"],
            state_reference=row["state_reference"],
            metadata_json=_json_loads(row["metadata_json"], {}),
        )

    # ── events and outbox ────────────────────────────────

    async def list_domain_events(
        self,
        aggregate_type: Optional[str] = None,
        aggregate_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        conn = self.db.connect()
        query = "SELECT * FROM domain_events WHERE 1=1"
        params: List[Any] = []
        if aggregate_type:
            query += " AND aggregate_type = ?"
            params.append(aggregate_type)
        if aggregate_id:
            query += " AND aggregate_id = ?"
            params.append(aggregate_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        query += " ORDER BY sequence_number ASC"
        if limit:
            query += f" LIMIT {int(limit)}"
        return [
            {
                "event_id": r["event_id"], "event_type": r["event_type"],
                "aggregate_type": r["aggregate_type"],
                "aggregate_id": r["aggregate_id"],
                "sequence_number": r["sequence_number"],
                "occurred_at": _str_to_dt(r["occurred_at"]),
                "recorded_at": _require_dt(r["recorded_at"]),
                "correlation_id": r["correlation_id"],
                "causation_id": r["causation_id"],
                "payload": _json_loads(r["payload"], {}),
            }
            for r in conn.execute(query, params).fetchall()
        ]

    async def count_domain_events(self) -> int:
        conn = self.db.connect()
        return conn.execute("SELECT COUNT(*) FROM domain_events").fetchone()[0]

    async def enqueue_outbox(self, message_type: str, payload: Dict[str, Any]) -> str:
        """Queue work for the dispatcher.

        The docstring contract is "inside the caller's transaction", but this
        method also commits when called standalone, because an uncommitted
        outbox row is silently lost on close — which is exactly the failure
        the transactional outbox exists to prevent. Callers that want the
        outbox row atomic with their own writes should pass their transaction
        connection via enqueue_outbox_in().
        """
        from uuid import uuid4
        conn = self.db.connect()
        message_id = str(uuid4())
        conn.execute(
            """INSERT INTO outbox_messages (id, message_type, payload, created_at)
               VALUES (?, ?, ?, ?)""",
            (message_id, message_type, _json_dumps(payload), _dt_to_str(_utcnow())),
        )
        conn.commit()
        return message_id

    def enqueue_outbox_in(
        self, conn: sqlite3.Connection, message_type: str, payload: Dict[str, Any]
    ) -> str:
        """Insert an outbox row on a caller-owned transaction connection.

        The caller commits, making the domain write and the outbox row atomic.
        """
        from uuid import uuid4
        message_id = str(uuid4())
        conn.execute(
            """INSERT INTO outbox_messages (id, message_type, payload, created_at)
               VALUES (?, ?, ?, ?)""",
            (message_id, message_type, _json_dumps(payload), _dt_to_str(_utcnow())),
        )
        return message_id

    async def pending_outbox(self) -> List[Dict[str, Any]]:
        conn = self.db.connect()
        rows = conn.execute(
            """SELECT * FROM outbox_messages WHERE published_at IS NULL
               ORDER BY created_at ASC"""
        ).fetchall()
        return [
            {
                "id": r["id"], "message_type": r["message_type"],
                "payload": _json_loads(r["payload"], {}),
                "delivery_attempts": r["delivery_attempts"],
            }
            for r in rows
        ]

    async def mark_outbox_published(self, message_id: str) -> None:
        conn = self.db.connect()
        conn.execute(
            "UPDATE outbox_messages SET published_at = ? WHERE id = ?",
            (_dt_to_str(_utcnow()), message_id),
        )
        conn.commit()
