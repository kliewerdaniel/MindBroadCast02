"""Material change detection.

Spec §9.2: "The system must distinguish the arrival of new content from the
arrival of new information." A new article does not necessarily mean the
underlying story has changed.

This module is the load-bearing distinction of Phase 4. Novelty (§10.3) and
temporal velocity (§10.4) are both computed from its output, so if this
mis-classifies repeated coverage as a development, every downstream ranking
inherits the error and the broadcaster announces the same thing repeatedly —
the exact failure §9.2 names.

The ten change kinds are §9.2's list, verbatim. Significance is recorded
per-change rather than folded into one score, because §9.4 requires uncertainty
to attach where it occurs.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class ChangeKind(Enum):
    """§9.2's list of what constitutes a material change."""
    NEW_EVENT = "new_event"
    CONFIRMED_DECISION = "confirmed_decision"
    NEW_MEASUREMENT = "new_measurement"
    REVISED_NUMBER = "revised_number"
    NEW_PARTICIPANT = "new_participant"
    NEW_CAUSAL_MECHANISM = "new_causal_mechanism"
    SIGNIFICANT_CONTRADICTION = "significant_contradiction"
    CORRECTION = "correction"
    UNRESOLVED_QUESTION_RESOLVED = "unresolved_question_resolved"
    RELATED_DEVELOPMENT = "related_development"


# Relative significance weights. These are tunable parameters, not universal
# constants (§10.3) — the default is equal weighting, and every decision
# records the weight set used so a later change is auditable.
DEFAULT_WEIGHTS: dict[ChangeKind, float] = {
    ChangeKind.NEW_EVENT: 1.0,
    ChangeKind.CONFIRMED_DECISION: 1.0,
    ChangeKind.NEW_MEASUREMENT: 0.8,
    ChangeKind.REVISED_NUMBER: 0.9,
    ChangeKind.NEW_PARTICIPANT: 0.5,
    ChangeKind.NEW_CAUSAL_MECHANISM: 0.7,
    ChangeKind.SIGNIFICANT_CONTRADICTION: 1.0,
    ChangeKind.CORRECTION: 1.0,
    ChangeKind.UNRESOLVED_QUESTION_RESOLVED: 0.8,
    ChangeKind.RELATED_DEVELOPMENT: 0.6,
}


@dataclass
class MaterialChange:
    kind: ChangeKind
    significance: float
    occurred_at: datetime
    detail: str = ""
    subject_id: str | None = None      # claim / event / entity this concerns
    evidence_id: str | None = None
    supersedes: str | None = None

    @property
    def is_material(self) -> bool:
        return self.significance > 0.0


@dataclass
class ChangeReport:
    changes: list[MaterialChange] = field(default_factory=list)
    evaluated_at: datetime = field(
        default_factory=lambda: datetime.now(UTC))
    article_count: int = 0

    @property
    def has_material_change(self) -> bool:
        return any(c.is_material for c in self.changes)

    @property
    def total_significance(self) -> float:
        return sum(c.significance for c in self.changes if c.is_material)

    def by_kind(self, kind: ChangeKind) -> list[MaterialChange]:
        return [c for c in self.changes if c.kind is kind]


@dataclass
class KnownState:
    """What the system already knows about a story.

    This is the "existing world model" that §10.3 says novelty must be
    measured against — not the article text.
    """
    claim_ids: set[str] = field(default_factory=set)
    claim_texts: dict[str, str] = field(default_factory=dict)
    event_ids: set[str] = field(default_factory=set)
    evidence_ids: set[str] = field(default_factory=set)
    entity_names: set[str] = field(default_factory=set)
    quantities: set[str] = field(default_factory=set)   # "value:unit" keys
    open_question_ids: set[str] = field(default_factory=set)
    corrected_claim_ids: set[str] = field(default_factory=set)
    last_material_update_at: datetime | None = None


@dataclass
class IncomingState:
    """What a new compilation contributes."""
    article_id: str = ""
    claims: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    corrections: list[dict[str, Any]] = field(default_factory=list)
    observed_at: datetime | None = None


class ChangeDetector:
    """Detects material changes by diffing incoming state against known state.

    Deliberately holds no database state: the caller supplies both sides, so
    the comparison is a pure, testable function and never queries from inside
    a transaction.
    """

    def __init__(self, weights: dict[ChangeKind, float] | None = None):
        self.weights = weights or dict(DEFAULT_WEIGHTS)

    def detect(self, known: KnownState,
               incoming: IncomingState) -> ChangeReport:
        report = ChangeReport(article_count=1)
        observed = incoming.observed_at or datetime.now(UTC)

        # A completely new event.
        for event in incoming.events:
            event_id = event.get("id")
            if event_id and event_id not in known.event_ids:
                report.changes.append(MaterialChange(
                    kind=ChangeKind.NEW_EVENT,
                    significance=self.weights[ChangeKind.NEW_EVENT],
                    occurred_at=observed,
                    detail=event.get("title", "new event"),
                    subject_id=event_id,
                ))

        # New claims, and whether they revise a previously reported number.
        incoming_quantities: set[str] = set()
        for claim in incoming.claims:
            claim_id = claim.get("id")
            text = claim.get("proposition", "")
            if claim_id and claim_id in known.claim_ids:
                continue

            # §9.2: the repetition check comes FIRST, before any type-specific
            # branch. A restated proposition is not new information regardless
            # of its type — checking quantity or causal structure first would
            # classify a repeated figure as a revision or a new measurement,
            # which is exactly the article-volume-as-development error.
            normalized = _normalize(text)
            if normalized and normalized in {
                    _normalize(t) for t in known.claim_texts.values()}:
                continue

            claim_type = claim.get("claim_type", "")
            for quantity in claim.get("quantities", []):
                key = f"{quantity.get('value')}:{quantity.get('unit')}"
                incoming_quantities.add(key)

            if claim_type == "quantitative":
                quantities = claim.get("quantities", [])
                if quantities:
                    key = (f"{quantities[0].get('value')}:"
                           f"{quantities[0].get('unit')}")
                    if known.quantities and key not in known.quantities:
                        # A number the system has not seen before, when it has
                        # seen numbers for this subject before, is a revision.
                        report.changes.append(MaterialChange(
                            kind=ChangeKind.REVISED_NUMBER,
                            significance=self.weights[
                                ChangeKind.REVISED_NUMBER],
                            occurred_at=observed,
                            detail=f"{key} (previously: "
                                   f"{sorted(known.quantities)})",
                            subject_id=claim_id,
                        ))
                        continue
                report.changes.append(MaterialChange(
                    kind=ChangeKind.NEW_MEASUREMENT,
                    significance=self.weights[ChangeKind.NEW_MEASUREMENT],
                    occurred_at=observed,
                    detail=text[:120],
                    subject_id=claim_id,
                ))
                continue

            if claim_type == "causal":
                report.changes.append(MaterialChange(
                    kind=ChangeKind.NEW_CAUSAL_MECHANISM,
                    significance=self.weights[
                        ChangeKind.NEW_CAUSAL_MECHANISM],
                    occurred_at=observed,
                    detail=text[:120],
                    subject_id=claim_id,
                ))
                continue

            report.changes.append(MaterialChange(
                kind=(ChangeKind.CONFIRMED_DECISION
                      if _is_decision(text) else ChangeKind.NEW_EVENT),
                significance=self.weights[ChangeKind.CONFIRMED_DECISION],
                occurred_at=observed,
                detail=text[:120],
                subject_id=claim_id,
            ))

        # New evidence for an existing claim is not a new claim, but it does
        # change what supports the account.
        for evidence in incoming.evidence:
            evidence_id = evidence.get("id")
            if evidence_id and evidence_id not in known.evidence_ids:
                report.changes.append(MaterialChange(
                    kind=ChangeKind.NEW_MEASUREMENT
                    if evidence.get("evidence_type") == "passage"
                    else ChangeKind.RELATED_DEVELOPMENT,
                    significance=self.weights[ChangeKind.NEW_MEASUREMENT]
                    * 0.5,          # supporting evidence, weaker than a claim
                    occurred_at=observed,
                    detail=f"new {evidence.get('evidence_type', 'evidence')}",
                    subject_id=evidence.get("claim_id"),
                    evidence_id=evidence_id,
                ))

        # Newly identified participants.
        for name in incoming.entities:
            if name not in known.entity_names:
                report.changes.append(MaterialChange(
                    kind=ChangeKind.NEW_PARTICIPANT,
                    significance=self.weights[ChangeKind.NEW_PARTICIPANT],
                    occurred_at=observed,
                    detail=name,
                ))

        # Corrections are material by definition.
        for correction in incoming.corrections:
            report.changes.append(MaterialChange(
                kind=ChangeKind.CORRECTION,
                significance=self.weights[ChangeKind.CORRECTION],
                occurred_at=observed,
                detail=correction.get("reason", "correction"),
                subject_id=correction.get("target_id"),
                supersedes=correction.get("target_id"),
            ))

        # An open question that a new claim speaks to is resolved.
        if known.open_question_ids and report.changes:
            for question_id in list(known.open_question_ids)[:1]:
                report.changes.append(MaterialChange(
                    kind=ChangeKind.UNRESOLVED_QUESTION_RESOLVED,
                    significance=self.weights[
                        ChangeKind.UNRESOLVED_QUESTION_RESOLVED],
                    occurred_at=observed,
                    detail="new information bears on an open question",
                    subject_id=question_id,
                ))

        return report

    def weight_set_id(self) -> str:
        """A stable identifier for the weights used, recorded per decision."""
        import hashlib
        canonical = "|".join(
            f"{k.value}:{v}" for k, v in sorted(
                self.weights.items(), key=lambda kv: kv[0].value))
        return hashlib.sha256(canonical.encode()).hexdigest()[:12]


_DECISION_MARKERS = (
    "approved", "passed", "rejected", "voted", "ratified", "signed",
    "enacted", "ruled", "ordered", "announced that it will",
    "confirmed that", "decided to", "agreed to",
)


def _is_decision(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _DECISION_MARKERS)


def _normalize(text: str) -> str:
    import re
    return re.sub(r"[^\w\s]", "", text.lower()).strip()
