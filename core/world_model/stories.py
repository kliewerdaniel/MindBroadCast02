"""Story engine: clustering developments into editorial stories.

Spec §5.5.1: "An event is a modeled occurrence; a story may encompass multiple
related events, articles, claims, and questions." The system "should not create
a completely unrelated editorial identity for every article about the proposal."

§9.1: a database of articles is not a model of the world — the model must
represent relationships and changes.

Clustering is deterministic: canonical identity (reusing the CKU identity from
Phase 3, which is qualifier-aware) plus shared entity overlap. No embeddings,
because §8.4 says semantic retrieval supplements structured relationships rather
than replacing them, and a clustering that cannot explain itself cannot be
audited under Gate 5.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from ..models import Story, StoryStatus
from ..persistence import WorldStore
from .changes import ChangeDetector, ChangeKind, KnownState, IncomingState
from .coverage import CoverageFacts, CoverageModel
from .ranking import StoryFacts, StoryRanker, RankingResult


@dataclass
class ClaimRecord:
    """A stored claim as the story engine sees it."""
    claim_id: str
    proposition: str
    claim_type: str
    epistemic_status: str
    quantities: List[Dict[str, Any]] = field(default_factory=list)
    temporals: List[Dict[str, Any]] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    first_observed_at: Optional[datetime] = None
    corrected_by: Optional[str] = None


@dataclass
class StoryCandidate:
    """A story assembled from related records."""
    story_id: str
    canonical_title: str
    claim_ids: List[str] = field(default_factory=list)
    event_ids: List[str] = field(default_factory=list)
    entity_names: Set[str] = field(default_factory=set)
    identity_keys: Set[str] = field(default_factory=set)
    unresolved_questions: List[str] = field(default_factory=list)
    last_material_update_at: Optional[datetime] = None

    def overlaps(self, other: "StoryCandidate") -> bool:
        """Whether two candidates describe the same development.

        Two signals, either sufficient: a shared canonical identity (same
        proposition with the same qualifiers) or meaningful entity overlap.
        """
        if self.identity_keys & other.identity_keys:
            return True
        if len(self.entity_names & other.entity_names) >= 2:
            return True
        return False


class StoryEngine:
    """Assembles stories from compiled claims and detects their developments."""

    # Minimum shared entities for an entity-only match. One shared entity is
    # too weak: "the government" appears in unrelated reporting.
    MIN_SHARED_ENTITIES = 2

    def __init__(
        self,
        store: WorldStore,
        change_detector: Optional[ChangeDetector] = None,
        ranker: Optional[StoryRanker] = None,
        coverage: Optional[CoverageModel] = None,
    ):
        self.store = store
        self.change_detector = change_detector or ChangeDetector()
        self.ranker = ranker or StoryRanker()
        self.coverage = coverage or CoverageModel()

    # ── clustering ───────────────────────────────────────

    def cluster(self, claims: List[ClaimRecord]) -> List[StoryCandidate]:
        """Group claims into story candidates by shared identity or entities."""
        candidates: List[StoryCandidate] = []
        for claim in claims:
            key = _identity_key(claim)
            candidate = StoryCandidate(
                story_id=f"candidate-{key[:12]}",
                canonical_title=claim.proposition[:120],
                claim_ids=[claim.claim_id],
                entity_names=set(claim.entities),
                identity_keys={key},
                last_material_update_at=claim.first_observed_at,
            )
            merged = False
            for existing in candidates:
                if candidate.overlaps(existing):
                    existing.claim_ids.append(claim.claim_id)
                    existing.entity_names |= candidate.entity_names
                    existing.identity_keys |= candidate.identity_keys
                    if (claim.first_observed_at and
                            (existing.last_material_update_at is None
                             or claim.first_observed_at
                             > existing.last_material_update_at)):
                        existing.last_material_update_at = \
                            claim.first_observed_at
                    merged = True
                    break
            if not merged:
                candidates.append(candidate)
        return candidates

    # ── known state for change detection ─────────────────

    def known_state_for(self, story: Story,
                        claims: List[ClaimRecord]) -> KnownState:
        """What the system already knows about a story (§10.3's baseline)."""
        return KnownState(
            claim_ids={c.claim_id for c in claims},
            claim_texts={c.claim_id: c.proposition for c in claims},
            quantities={
                f"{q.get('value')}:{q.get('unit')}"
                for c in claims for q in c.quantities
            },
            entity_names={
                e for c in claims for e in c.entities
            },
            corrected_claim_ids={
                c.claim_id for c in claims if c.corrected_by
            },
            open_question_ids=set(),
            last_material_update_at=story.last_material_update_at,
        )

    # ── ranking from stored records ──────────────────────

    def story_facts(
        self,
        story: Story,
        claims: List[ClaimRecord],
        material_changes: List[Any],
        evidence_counts: Optional[Dict[str, int]] = None,
        source_groups: Tuple[int, int] = (0, 1),
        now: Optional[datetime] = None,
    ) -> StoryFacts:
        """Build ranking facts from stored records only — no live model."""
        evidence_counts = evidence_counts or {}
        contradicted = sum(
            1 for c in claims if c.epistemic_status == "contradicted")
        corrected = sum(1 for c in claims if c.corrected_by)

        return StoryFacts(
            story_id=story.id,
            title=story.canonical_title,
            now=now,
            material_changes=material_changes,
            last_material_update_at=story.last_material_update_at,
            last_substantial_coverage_at=story.last_substantial_coverage_at,
            primary_evidence_count=evidence_counts.get("primary", 0),
            secondary_evidence_count=evidence_counts.get("secondary", 0),
            attributed_evidence_count=evidence_counts.get("attributed", 0),
            independent_source_groups=source_groups[0],
            total_source_groups=source_groups[1],
            contradicted_claim_count=contradicted,
            corrected_evidence_count=corrected,
            unresolved_question_count=story.unresolved_question_count,
            unresolved_question_materiality=0.5,
            audience_relevance=_audience_value(
                story.audience_relevance_state),
            prior_coverage_depth=0,
        )

    def coverage_facts(self, story: Story, has_material_change: bool,
                       now: Optional[datetime] = None) -> CoverageFacts:
        return CoverageFacts(
            story_id=story.id,
            importance=0.0,
            prior_coverage_depth=0,
            last_substantial_coverage_at=story.last_substantial_coverage_at,
            unresolved_question_count=story.unresolved_question_count,
            has_material_change=has_material_change,
            now=now,
        )

    # ── persistence ──────────────────────────────────────

    async def persist_story(self, candidate: StoryCandidate) -> str:
        """Create a story record for a candidate cluster."""
        story = Story(
            canonical_title=candidate.canonical_title,
            editorial_summary="",
            status=StoryStatus.ACTIVE,
            last_material_update_at=(candidate.last_material_update_at
                                     or datetime.now(timezone.utc)),
            claim_ids=list(candidate.claim_ids),
            unresolved_question_count=len(candidate.unresolved_questions),
        )
        return await self.store.add_story(story)


def _identity_key(claim: ClaimRecord) -> str:
    """Qualifier-aware identity, shared with the Phase 3 CKU.

    Two differently worded propositions with the same qualifiers share a key;
    identical sentences with different quantities do not (§8.3.1).
    """
    import hashlib
    parts = [
        claim.claim_type,
        _normalize(claim.proposition)[:80],
    ]
    for quantity in claim.quantities:
        parts.append(f"q:{quantity.get('value')}:{_unit(quantity.get('unit'))}")
    for temporal in claim.temporals:
        parts.append(f"t:{(temporal.get('phrase') or '').lower()}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _normalize(text: str) -> str:
    import re
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _unit(unit: Optional[str]) -> str:
    if not unit:
        return ""
    text = unit.lower().strip()
    if text in ("per cent", "percent", "percentage"):
        return "%"
    return text


def _audience_value(state: str) -> float:
    return {"high": 1.0, "neutral": 0.3, "low": 0.0}.get(state or "neutral", 0.3)
