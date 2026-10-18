"""Legacy schema import.

Spec §26 / Phase 1 task C2: move the pre-Phase-1 7-table schema into the
Phase 1 model **without losing any timestamp or relationship**.

The legacy schema conflated Article with Event (events carried source_url,
fetched_at, content, extractor) and stored corrections as Evidence rows. This
importer therefore has to make real decisions, not just copy columns:

- A Source is synthesized from each distinct source_url domain.
- Legacy events become Articles (they were publication artifacts).
- A genuine Event is created per legacy story, since the legacy schema has no
  occurrence-level record.
- Legacy correction-shaped evidence rows become first-class Corrections.
- Every timestamp is preserved with its original meaning intact.
"""

import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime

from .models import (
    Article,
    Claim,
    ClaimStatus,
    ClaimType,
    Correction,
    CorrectionType,
    Entity,
    EntityType,
    Event,
    EventStatus,
    Evidence,
    EvidenceRelationship,
    ExtractionStatus,
    Source,
    SourceType,
    Story,
    claim_status_from_str,
)


def is_legacy_database(conn: sqlite3.Connection) -> bool:
    """True if this connection holds the pre-Phase-1 schema."""
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    return "events" in tables and "articles" not in tables


def import_legacy_schema(store, legacy_conn: sqlite3.Connection) -> dict[str, int]:
    """Import legacy rows into a Phase 1 WorldStore.

    Returns counts of imported records. `store` must already be migrated.

    Uses a private event loop rather than ``asyncio.get_event_loop()``. That
    call raises when a loop was created and closed earlier in the process (a
    prior test, or any code that ran ``asyncio.run``), which made this import
    depend on unrelated code having left the global loop alone. A private loop
    cannot be disturbed by, or disturb, anything else.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_import_async(store, legacy_conn))
    finally:
        loop.close()


async def _import_async(store, legacy_conn: sqlite3.Connection) -> dict[str, int]:
    legacy_conn.row_factory = sqlite3.Row
    counts = {
        "sources": 0, "articles": 0, "events": 0, "stories": 0,
        "claims": 0, "evidence": 0, "corrections": 0, "entities": 0,
    }
    source_by_domain: dict[str, str] = {}
    claim_id_map: dict[str, str] = {}

    # ── sources, synthesized from distinct domains ──────
    for url in _distinct_urls(legacy_conn):
        domain = _domain_from_url(url)
        if not domain or domain in source_by_domain:
            continue
        source = Source(
            name=domain,
            canonical_domain=domain,
            source_type=SourceType.OTHER,
            endpoint=f"https://{domain}",
        )
        source_by_domain[domain] = await store.add_source(source)
        counts["sources"] += 1

    # ── legacy events become articles ───────────────────
    for row in legacy_conn.execute("SELECT * FROM events").fetchall():
        source_id = source_by_domain.get(
            _domain_from_url(row["source_url"] or "") or "", "")
        if not source_id:
            continue
        article = Article(
            source_id=source_id,
            canonical_url=row["source_url"] or "",
            original_url=row["source_url"] or "",
            title=row["title"] or "",
            published_at=_parse(row["published_at"]),
            # A legacy row may lack both; the import records the gap in
            # metadata rather than fabricating a timestamp, which would
            # corrupt the valid/observation/transaction distinction (§5.9.1).
            first_seen_at=_legacy_ts(row, "fetched_at", "created_at"),
            fetched_at=_legacy_ts(row, "fetched_at", "created_at"),
            content_hash=_synthesized_hash(row),
            normalized_text=row["content"] or "",
            extraction_status=ExtractionStatus.SUCCEEDED,
            metadata_json={"legacy_event_id": row["event_id"]},
        )
        await store.add_article(article)
        counts["articles"] += 1

    # ── one genuine event per legacy story ──────────────
    for row in legacy_conn.execute("SELECT * FROM stories").fetchall():
        observed = _legacy_ts(row, "first_seen", "created_at")
        await store.add_event(Event(
            canonical_title=row["title"] or "",
            summary=row["summary"] or "",
            status=EventStatus.ACTIVE,
            first_observed_at=observed,
        ))
        counts["events"] += 1

        await store.add_story(Story(
            canonical_title=row["title"] or "",
            editorial_summary=row["summary"] or "",
            created_at=observed,
            confidence=row["confidence"] or 0.0,
            corrected_by=row["corrected_by"],
        ))
        counts["stories"] += 1

    # ── claims ──────────────────────────────────────────
    for row in legacy_conn.execute("SELECT * FROM claims").fetchall():
        try:
            status = claim_status_from_str(row["status"] or "unassessed")
        except ValueError:
            status = ClaimStatus.UNASSESSED
        new_id = await store.add_claim(Claim(
            canonical_proposition=row["claim_text"] or "",
            claim_type=ClaimType.OBSERVATIONAL,
            epistemic_status=status,
            first_observed_at=_legacy_ts(row, "created_at"),
            confidence=row["confidence"] or 0.0,
            corrected_by=row["corrected_by"],
        ))
        claim_id_map[row["claim_id"]] = new_id
        counts["claims"] += 1

    # ── evidence, or correction if it was one ───────────
    for row in legacy_conn.execute("SELECT * FROM evidence").fetchall():
        if (row["extraction_method"] or "") == "correction":
            await store.add_correction(Correction(
                correction_type=CorrectionType.PUBLISHER,
                target_type="claim",
                target_id=claim_id_map.get(row["claim_id"], row["claim_id"] or ""),
                reason=row["content"] or "",
                detected_at=_legacy_ts(row, "created_at"),
                original_value_reference=row["correction_of"] or "",
                metadata_json={"legacy_evidence_id": row["evidence_id"]},
            ))
            counts["corrections"] += 1
            continue

        evidence_id = await store.add_evidence(Evidence(
            evidence_type="passage",
            source_id=source_by_domain.get(
                _domain_from_url(row["source_url"] or "") or "", ""),
            excerpt=row["content"] or "",
            observed_at=_legacy_ts(row, "created_at"),
            extraction_method=row["extraction_method"] or "",
            metadata_json={"legacy_evidence_id": row["evidence_id"]},
        ))
        mapped_claim = claim_id_map.get(row["claim_id"])
        if mapped_claim:
            await store.link_evidence(
                mapped_claim, evidence_id, EvidenceRelationship.SUPPORTS)
        counts["evidence"] += 1

    # ── entities ────────────────────────────────────────
    for row in legacy_conn.execute("SELECT * FROM entities").fetchall():
        try:
            entity_type = EntityType(row["entity_type"] or "concept")
        except ValueError:
            entity_type = EntityType.CONCEPT
        await store.add_entity(Entity(
            canonical_name=row["name"] or "",
            entity_type=entity_type,
            aliases=_split_aliases(row["aliases"]),
            first_observed_at=_legacy_ts(row, "first_seen"),
            last_observed_at=_legacy_ts(row, "last_seen"),
            metadata_json={"legacy_entity_id": row["entity_id"]},
        ))
        counts["entities"] += 1

    return counts


# ── helpers ──────────────────────────────────────────────────────────


def _distinct_urls(conn: sqlite3.Connection):
    return [
        r[0] for r in conn.execute(
            """SELECT DISTINCT source_url FROM events
               WHERE source_url IS NOT NULL AND source_url != ''
               UNION
               SELECT DISTINCT source_url FROM claims
               WHERE source_url IS NOT NULL AND source_url != ''
               UNION
               SELECT DISTINCT source_url FROM evidence
               WHERE source_url IS NOT NULL AND source_url != ''"""
        ).fetchall()
    ]


def _domain_from_url(url: str) -> str | None:
    if not url:
        return None
    without_scheme = url.split("://", 1)[-1]
    return without_scheme.split("/", 1)[0] or None


def _parse(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _split_aliases(raw) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _legacy_ts(row, *columns) -> datetime:
    """First non-null legacy timestamp, falling back to the epoch.

    A legacy row may carry no timestamp at all. Substituting "now" would
    fabricate an observation time and corrupt the valid/observation/
    transaction distinction (§5.9.1), so the fallback is an explicit,
    obviously-wrong epoch value that flags the row for review rather than
    silently passing as fresh.
    """
    for column in columns:
        parsed = _parse(row[column])
        if parsed is not None:
            return parsed
    return datetime(1970, 1, 1)


def _synthesized_hash(row) -> str:
    """Legacy events have no content_hash; derive a stable one."""
    key = f"{row['event_id']}|{row['source_url']}|{row['title']}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
