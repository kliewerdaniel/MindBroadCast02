"""Canonical Knowledge Units (§8.3).

Spec guidance is explicit:

    The safest initial design is to treat CKUs as a stable interface over
    existing domain objects, rather than introducing a second competing system
    of record.

So there is deliberately **no `cku` table**. A CKU is assembled by querying
the Claim, Evidence, Entity, and Event records that already exist. If a second
store of truth were introduced, every invariant Phase 1 established — append-only
provenance, correction chains, snapshot completeness — would need enforcing
twice.

§8.3.1 requires the identity of a CKU not to depend exclusively on its text:

    Two differently worded propositions may express the same underlying
    assertion, while two nearly identical sentences may refer to different
    times or quantities.

So identity is (claim type + normalized subject + normalized predicate +
normalized object + quantity + temporal qualifier), not the sentence string.
Two sentences saying the same thing with the same qualifiers share a CKU; the
same sentence with different quantities does not.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..models import Claim, EvidenceRelationship


@dataclass
class CanonicalKnowledgeUnit:
    """A stable, addressable view over existing domain objects.

    Read-only: constructing one writes nothing.
    """
    id: str
    canonical_content: str
    unit_type: str                       # claim_backed | synthesized
    epistemic_status: str
    claim_id: str
    claim_type: str
    source_references: list[str] = field(default_factory=list)
    evidence_references: list[str] = field(default_factory=list)
    supporting_references: list[str] = field(default_factory=list)
    contradicting_references: list[str] = field(default_factory=list)
    attributing_references: list[str] = field(default_factory=list)
    entity_references: list[str] = field(default_factory=list)
    event_references: list[str] = field(default_factory=list)
    validity_interval: dict[str, str | None] = field(default_factory=dict)
    observation_interval: dict[str, str | None] = field(default_factory=dict)
    version: int = 1
    supersedes_version: str | None = None
    provenance_manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def is_supported(self) -> bool:
        return self.epistemic_status in ("supported", "corroborated")

    @property
    def has_counterevidence(self) -> bool:
        return bool(self.contradicting_references)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "canonical_content": self.canonical_content,
            "unit_type": self.unit_type,
            "epistemic_status": self.epistemic_status,
            "claim_id": self.claim_id,
            "claim_type": self.claim_type,
            "source_references": self.source_references,
            "evidence_references": self.evidence_references,
            "supporting_references": self.supporting_references,
            "contradicting_references": self.contradicting_references,
            "attributing_references": self.attributing_references,
            "entity_references": self.entity_references,
            "event_references": self.event_references,
            "validity_interval": self.validity_interval,
            "observation_interval": self.observation_interval,
            "version": self.version,
            "supersedes_version": self.supersedes_version,
            "provenance_manifest": self.provenance_manifest,
        }


def canonical_identity(
    claim: Claim,
    quantities: list[dict[str, Any]] | None = None,
    temporals: list[dict[str, Any]] | None = None,
) -> str:
    """The stable identity of a CKU (§8.3.1).

    Deliberately NOT the proposition text. Two differently worded sentences
    expressing the same assertion with the same qualifiers share an identity;
    identical sentences with different quantities do not.
    """
    parts = [
        claim.claim_type.value,
        (claim.normalized_subject_id or "").lower(),
        (claim.normalized_predicate or "").lower(),
        (claim.normalized_object or "").lower(),
    ]
    for quantity in (quantities or []):
        parts.append(f"q:{quantity.get('value')}:{_unit(quantity.get('unit'))}")
    for temporal in (temporals or []):
        parts.append(f"t:{(temporal.get('phrase') or '').lower()}")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


def _unit(unit: str | None) -> str:
    if not unit:
        return ""
    text = unit.lower().strip()
    if text in ("per cent", "percent", "percentage"):
        return "%"
    return text


class CKUAssembler:
    """Assembles CKUs from existing records. Writes nothing."""

    def __init__(self, store):
        self.store = store

    async def for_claim(self, claim: Claim) -> CanonicalKnowledgeUnit:
        evidence = await self.store.get_evidence_for_claim(claim.id)
        links = await self.store.get_evidence_links(claim.id)

        supporting, contradicting, attributing = [], [], []
        for link in links:
            if link.relationship_type is EvidenceRelationship.SUPPORTS:
                supporting.append(link.evidence_id)
            elif link.relationship_type is EvidenceRelationship.CONTRADICTS:
                contradicting.append(link.evidence_id)
            elif link.relationship_type is EvidenceRelationship.ATTRIBUTES:
                attributing.append(link.evidence_id)

        metadata = claim.metadata_json or {}
        identity = canonical_identity(
            claim,
            metadata.get("quantities"),
            metadata.get("temporal_qualifiers"),
        )

        sources = sorted({
            e.source_id for e in evidence if e.source_id
        })

        return CanonicalKnowledgeUnit(
            id=f"cku-{identity}",
            canonical_content=claim.canonical_proposition,
            unit_type="claim_backed",
            epistemic_status=claim.epistemic_status.value,
            claim_id=claim.id,
            claim_type=claim.claim_type.value,
            source_references=sources,
            evidence_references=[e.id for e in evidence],
            supporting_references=supporting,
            contradicting_references=contradicting,
            attributing_references=attributing,
            entity_references=list(metadata.get("entity_ids", [])),
            event_references=list(metadata.get("event_ids", [])),
            validity_interval={
                "start": _iso(claim.valid_time_start),
                "end": _iso(claim.valid_time_end),
            },
            observation_interval={
                "first_observed": _iso(claim.first_observed_at),
                "last_evaluated": _iso(claim.last_evaluated_at),
            },
            version=1,
            supersedes_version=claim.supersedes_claim_id,
            provenance_manifest={
                "created_by_component": claim.created_by_component,
                "extraction_confidence": metadata.get(
                    "extraction_confidence", claim.confidence),
                "article_id": metadata.get("article_id"),
                "span": metadata.get("span"),
                # Extraction confidence is recorded separately from
                # evidentiary support — they are not the same quantity (§8.2).
                "assessment_methods": sorted({
                    link.assessment_method for link in links
                    if link.assessment_method
                }),
            },
        )

    async def group_by_identity(self, claims: list[Claim]) -> dict[str, list[Claim]]:
        """Group claims that share a canonical identity.

        This is what lets several articles support one CKU without the system
        treating each restatement as new knowledge.
        """
        groups: dict[str, list[Claim]] = {}
        for claim in claims:
            metadata = claim.metadata_json or {}
            identity = canonical_identity(
                claim,
                metadata.get("quantities"),
                metadata.get("temporal_qualifiers"),
            )
            groups.setdefault(f"cku-{identity}", []).append(claim)
        return groups


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
