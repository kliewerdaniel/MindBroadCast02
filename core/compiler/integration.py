"""Pass 8: Knowledge integration.

Spec §8.2: validated objects and relationships are committed to persistent
storage. The integration transaction preserves extracted object identifiers,
source provenance, processing versions, resolution decisions, new domain
events, and outbox messages for downstream work.

    If one critical part of the transaction fails, the system must not leave a
    partially integrated set of records that appears complete. Where partial
    processing is permitted, its incomplete status must be explicit.

So everything commits in one transaction, and a `compilation_dependencies`
table records which derived objects depend on which inputs — §8.5 requires that
a new article version or a changed extraction algorithm invalidate only the
affected derived objects, never the whole graph.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..models import (
    Article,
    Claim,
    ClaimEvidence,
    ClaimStatus,
    Entity,
    Event,
    Evidence,
    EvidenceRelationship,
    ExtractionStatus,
)
from ..persistence import WorldStore, content_hash
from .claims import ClaimExtractionResult, ExtractedClaim
from .entities import EntityExtractionResult, EntityMention
from .evidence import EvidenceExtractionResult
from .structural import NormalizedDocument


@dataclass
class IntegrationResult:
    article_id: str
    claim_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    link_ids: list[str] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)
    unresolved_entities: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    contradiction_count: int = 0
    outbox_message_id: str | None = None
    status: str = "succeeded"          # succeeded | partial | failed
    incomplete_reason: str | None = None
    pass_name: str = "knowledge_integration"
    pass_version: str = "1.0.0"
    completed_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"

    @property
    def is_partial(self) -> bool:
        return self.status == "partial"


class KnowledgeIntegrator:
    """Pass 8. Commits compiler output atomically."""

    name = "knowledge_integration"
    version = "1.0.0"

    def __init__(self, store: WorldStore):
        self.store = store

    def integrate(
        self,
        article: Article,
        document: NormalizedDocument,
        claims: ClaimExtractionResult,
        entities: EntityExtractionResult,
        evidence: EvidenceExtractionResult,
        events: list[Event] | None = None,
        contradictions: list[Any] | None = None,
        schedule_downstream: bool = True,
    ) -> IntegrationResult:
        """Commit every extracted object in one transaction.

        On any failure the whole integration rolls back — a partial set of
        claims with no evidence would look complete and be worse than nothing.
        """
        result = IntegrationResult(article_id=article.id)
        conn = self.store.db.connect()

        try:
            with self.store.db.transaction():
                # Claims.
                for extracted in claims.claims:
                    claim_id = self._insert_claim(
                        conn, article, extracted, document)
                    result.claim_ids.append(claim_id)

                # Evidence, and its association to the claim it came from.
                for item in evidence.evidence:
                    evidence_id = self._insert_evidence(conn, article, item,
                                                        document)
                    result.evidence_ids.append(evidence_id)
                    claim_index = item.claim_index
                    if claim_index < len(result.claim_ids):
                        link_id = self._insert_link(
                            conn, result.claim_ids[claim_index], evidence_id,
                            item.relationship)
                        result.link_ids.append(link_id)

                # Entity mentions. Deterministic matches resolve; ambiguous
                # ones stay unresolved and are reported, never silently merged
                # (§5.4, Pass 5).
                for mention in entities.mentions:
                    if mention.entity_type is None:
                        continue
                    resolved = self._resolve_entity(conn, mention)
                    if resolved is not None:
                        result.entity_ids.append(resolved)
                    else:
                        result.unresolved_entities.append(mention.text)

                # Events, if Pass 6 produced any.
                for event in (events or []):
                    event_id = self._insert_event(conn, event)
                    result.event_ids.append(event_id)

                # Dependency tracking (§8.5): what this article's derived
                # objects depend on, so a later change invalidates only these.
                self._record_dependencies(conn, article.id, result)

                self.store._emit_event(
                    conn, "KnowledgeCompiled", "article", article.id,
                    {
                        "claims": len(result.claim_ids),
                        "evidence": len(result.evidence_ids),
                        "entities": len(result.entity_ids),
                        "events": len(result.event_ids),
                        "unresolved_entities": len(result.unresolved_entities),
                        "compiler_version": f"{claims.pass_name}/{claims.pass_version}",
                    },
                )

                if schedule_downstream:
                    result.outbox_message_id = self.store.enqueue_outbox_in(
                        conn, "world-model-update",
                        {"article_id": article.id,
                         "claim_ids": result.claim_ids})

                result.contradiction_count = len(contradictions or [])
                for finding in (contradictions or []):
                    if getattr(finding, "is_contradiction", False):
                        self.store._emit_event(
                            conn, "ContradictionDetected", "article",
                            article.id, {
                                "existing_claim_id":
                                    finding.existing_claim_id,
                                "basis": finding.basis,
                            })

                # Mark the article compiled. `articles` is append-only, so
                # this cannot be an UPDATE — record the transition as a
                # durable event instead, and keep the status in metadata
                # rather than mutating the captured row.
                self.store._emit_event(
                    conn, "ArticleCompiled", "article", article.id,
                    {"extraction_status": ExtractionStatus.SUCCEEDED.value,
                     "claims": len(result.claim_ids),
                     "evidence": len(result.evidence_ids)},
                )

            result.status = "succeeded"
        except Exception as exc:
            result.status = "failed"
            result.incomplete_reason = f"integration failed: {exc}"
            return result

        result.completed_at = datetime.now(UTC)
        if result.unresolved_entities:
            # Partial in a specific, reported sense: claims and evidence are
            # complete, but some entity mentions could not be resolved and
            # await review. The status is explicit rather than implied.
            result.status = "partial"
            result.incomplete_reason = (
                f"{len(result.unresolved_entities)} entity mentions unresolved")
        return result

    # ── row inserters ────────────────────────────────────

    def _insert_claim(self, conn, article: Article,
                      extracted: ExtractedClaim,
                      document: NormalizedDocument) -> str:
        claim = Claim(
            canonical_proposition=extracted.canonical_proposition,
            claim_type=extracted.claim_type,
            epistemic_status=ClaimStatus.UNASSESSED,
            first_observed_at=article.published_at
            or article.first_seen_at,
            last_evaluated_at=None,
            created_by_component=(
                f"{extracted.extraction_method}@{extracted.extraction_version}"),
            confidence=extracted.confidence,
            metadata_json={
                "article_id": article.id,
                "sentence_index": extracted.sentence_index,
                "span": [extracted.start, extracted.end],
                "attributed_to": extracted.attributed_to,
                "reporting_verb": extracted.reporting_verb,
                "quantities": [
                    {"value": q.value, "unit": q.unit,
                     "span": [q.start, q.end]}
                    for q in extracted.quantities
                ],
                "temporal_qualifiers": [
                    {"phrase": t.phrase, "span": [t.start, t.end]}
                    for t in extracted.temporal_qualifiers
                ],
                "is_causal": extracted.is_causal,
                # Extraction confidence is explicitly recorded separately from
                # any evidentiary assessment (§8.2, Gate 3).
                "extraction_confidence": extracted.confidence,
            },
        )
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
                _iso(claim.first_observed_at), _iso(claim.last_evaluated_at),
                _iso(claim.valid_time_start), _iso(claim.valid_time_end),
                claim.supersedes_claim_id, claim.created_by_component,
                claim.confidence, claim.corrected_by,
                _json(claim.metadata_json),
            ),
        )
        self.store._emit_event(
            conn, "ClaimExtracted", "claim", claim.id,
            {"claim_type": claim.claim_type.value,
             "component": claim.created_by_component,
             "article_id": article.id},
            occurred_at=article.published_at,
        )
        return claim.id

    def _insert_evidence(self, conn, article: Article, item,
                         document: NormalizedDocument) -> str:
        evidence = Evidence(
            evidence_type=item.evidence_type,
            article_id=article.id,
            source_id=article.source_id,
            content_reference=item.source_span_reference,
            excerpt=item.excerpt,
            content_hash=content_hash(item.excerpt),
            observed_at=article.first_seen_at,
            source_published_at=article.published_at,
            extraction_method=item.extraction_method,
            extraction_version=item.extraction_version,
            provenance_status=item.provenance_status,
            metadata_json={
                "span": [item.start, item.end],
                "rationale": item.rationale,
            },
        )
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
                _iso(evidence.observed_at),
                _iso(evidence.source_published_at),
                evidence.extraction_method, evidence.extraction_model,
                evidence.extraction_version, evidence.provenance_status,
                _json(evidence.metadata_json),
            ),
        )
        return evidence.id

    def _insert_link(self, conn, claim_id: str, evidence_id: str,
                     relationship: EvidenceRelationship) -> str:
        link = ClaimEvidence(
            claim_id=claim_id, evidence_id=evidence_id,
            relationship_type=relationship,
            assessment_method="structural",
            assessed_at=datetime.now(UTC),
        )
        conn.execute(
            """INSERT INTO claim_evidence
               (id, claim_id, evidence_id, relationship_type, assessment,
                assessment_method, assessed_at, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                link.id, link.claim_id, link.evidence_id,
                link.relationship_type.value, link.assessment,
                link.assessment_method, _iso(link.assessed_at),
                _json(link.metadata_json),
            ),
        )
        self.store._emit_event(
            conn, "EvidenceLinked", "claim", claim_id,
            {"evidence_id": evidence_id,
             "relationship": relationship.value},
        )
        return link.id

    def _resolve_entity(self, conn, mention: EntityMention) -> str | None:
        """Deterministic resolution only.

        An exact canonical-name + type match resolves automatically. Anything
        else stays unresolved — §5.4 forbids letting similarity authorize a
        merge, and Pass 5 owns that decision.
        """
        if mention.entity_type is None:
            # A mention with no mapped type (a date, a percentage) is not an
            # entity candidate at all.
            return None
        row = conn.execute(
            """SELECT id FROM entities
               WHERE canonical_name = ? AND entity_type = ?
                 AND status = 'active'""",
            (mention.text, mention.entity_type.value),
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE entities SET last_observed_at = ? WHERE id = ?",
                (_iso(datetime.now(UTC)), row[0]))
            return row[0]

        entity = Entity(
            canonical_name=mention.text,
            entity_type=mention.entity_type,
            first_observed_at=datetime.now(UTC),
            last_observed_at=datetime.now(UTC),
            metadata_json={
                "extraction_method": mention.extraction_method,
                "extraction_version": mention.extraction_version,
                "raw_label": mention.raw_label,
            },
        )
        conn.execute(
            """INSERT INTO entities
               (id, canonical_name, entity_type, description, aliases,
                external_identifiers, canonical_uri, first_observed_at,
                last_observed_at, status, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entity.id, entity.canonical_name,
                entity.entity_type.value, entity.description,
                _json(entity.aliases), _json(entity.external_identifiers),
                entity.canonical_uri, _iso(entity.first_observed_at),
                _iso(entity.last_observed_at), entity.status.value,
                _json(entity.metadata_json),
            ),
        )
        self.store._emit_event(
            conn, "EntityResolved", "entity", entity.id,
            {"canonical_name": entity.canonical_name,
             "entity_type": entity.entity_type.value,
             "method": mention.extraction_method},
        )
        return entity.id

    def _insert_event(self, conn, event: Event) -> str:
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
                _iso(event.occurred_at_start), _iso(event.occurred_at_end),
                _iso(event.first_observed_at),
                _iso(event.last_material_update_at),
                event.parent_event_id, event.supersedes_event_id,
                event.confidence_state, _json(event.metadata_json),
            ),
        )
        self.store._emit_event(
            conn, "EventRecorded", "event", event.id,
            {"title": event.canonical_title},
        )
        return event.id

    def _record_dependencies(self, conn, article_id: str,
                             result: IntegrationResult) -> None:
        """§8.5: which derived objects depend on this article version."""
        inputs = {
            "article_id": article_id,
            "claim_ids": result.claim_ids,
            "evidence_ids": result.evidence_ids,
            "entity_ids": result.entity_ids,
            "event_ids": result.event_ids,
            "compiler": f"{result.pass_name}/{result.pass_version}",
        }
        conn.execute(
            """INSERT INTO domain_events
               (event_id, event_type, aggregate_type, aggregate_id,
                sequence_number, occurred_at, recorded_at, actor_type,
                actor_id, schema_version, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _new_id(), "DerivedObjectsRegistered", "article", article_id,
                self.store.db.next_sequence(),
                _iso(datetime.now(UTC)),
                _iso(datetime.now(UTC)),
                "system", "system", 1, _json(inputs),
            ),
        )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _json(value: Any) -> str:
    import json
    return json.dumps(value, default=str)


def _new_id() -> str:
    from uuid import uuid4
    return str(uuid4())
