"""Editorial ranking.

Spec §10.1 gives the objective:

    P(s,t) = f(I, N, V, E, R, U, A, C, D)

and then says the nine variables "must be operationally defined before being
combined into a production ranking." This module is that operational
definition. Every factor is a pure function of stored state, computable
without a language model — spec §22.2 requires exactly that ("a story-ranking
test should be able to verify that a material correction increases a story's
eligibility without requiring a live language model").

Three properties are enforced by construction:

1. **Novelty is measured against the world model, not article text** (§10.3).
   It is a diff of what changed since last coverage, so ten restatements score
   zero.

2. **Temporal velocity uses material developments, not article count** (§10.4).
   The decay term uses observation time, so a late discovery about an old
   event is not ranked as stale (§9.3).

3. **Audience relevance is bounded and cannot override importance or evidence
   quality** (§10.7). It enters as a bounded additive term, never a multiplier.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .changes import ChangeKind, MaterialChange


# Default factor weights. Tunable parameters, not universal constants (§10.3).
# Equal weighting is the documented default; every recorded decision carries
# the weight-set id so a later change is auditable.
DEFAULT_WEIGHTS: Dict[str, float] = {
    "importance": 1.0,
    "novelty": 1.0,
    "velocity": 0.8,
    "evidence": 1.0,
    "relationship": 0.3,
    "unresolved": 0.6,
    "audience": 0.4,
    "continuity": 0.3,
}

# Penalty weights from §10.10.
DEFAULT_LAMBDA_REDUNDANCY = 0.7
DEFAULT_MU_TRANSITION = 0.2

# Velocity decay (§10.4). Varies by story type; this is the default tau.
DEFAULT_TAU_SECONDS = 6 * 3600        # six hours

# Audience relevance is capped so it cannot dominate importance or evidence.
MAX_AUDIENCE_CONTRIBUTION = 0.5


@dataclass
class StoryFacts:
    """Everything the ranker needs about one story, from stored records."""
    story_id: str
    title: str = ""
    now: Optional[datetime] = None

    # Importance inputs (§10.2)
    people_affected: Optional[int] = None
    economic_scale: Optional[float] = None
    is_irreversible: bool = False
    has_safety_implication: bool = False
    geopolitical_reach: float = 0.0

    # Novelty / velocity inputs (§10.3, §10.4)
    material_changes: List[MaterialChange] = field(default_factory=list)
    article_count_since_coverage: int = 0
    last_material_update_at: Optional[datetime] = None
    last_substantial_coverage_at: Optional[datetime] = None
    story_type: str = "general"

    # Evidence inputs (§10.6)
    primary_evidence_count: int = 0
    secondary_evidence_count: int = 0
    attributed_evidence_count: int = 0
    independent_source_groups: int = 0
    total_source_groups: int = 1
    contradicted_claim_count: int = 0
    corrected_evidence_count: int = 0

    # Relationship inputs (§10.5)
    degree_centrality: int = 0
    max_degree_in_graph: int = 1
    high_impact_connections: int = 0

    # Unresolved questions (§10.8)
    unresolved_question_count: int = 0
    unresolved_question_materiality: float = 0.0

    # Audience (§10.7) — bounded
    audience_relevance: float = 0.0

    # Continuity / redundancy (§10.8, §10.9)
    is_currently_discussed: bool = False
    prior_coverage_depth: int = 0
    recent_segments_repeated: bool = False


@dataclass
class FactorValue:
    name: str
    value: float
    weight: float
    reason: str = ""

    @property
    def contribution(self) -> float:
        return self.value * self.weight


@dataclass
class RankingResult:
    story_id: str
    score: float
    factors: List[FactorValue] = field(default_factory=list)
    redundancy_penalty: float = 0.0
    transition_cost: float = 0.0
    reasons: List[str] = field(default_factory=list)
    weight_set_id: str = ""
    computed_at: Optional[datetime] = None

    @property
    def net_value(self) -> float:
        return (self.score
                - DEFAULT_LAMBDA_REDUNDANCY * self.redundancy_penalty
                - DEFAULT_MU_TRANSITION * self.transition_cost)

    def to_decision_record(self, decision: str = "SELECTED") -> Dict[str, Any]:
        """§10.11: the structured decision record.

        This is the source of truth. A human-readable explanation is derived
        from it, never a substitute for it.
        """
        return {
            "story_id": self.story_id,
            "decision": decision,
            "score": round(self.score, 4),
            "net_value": round(self.net_value, 4),
            "factors": {
                f.name: {
                    "value": round(f.value, 4),
                    "weight": f.weight,
                    "contribution": round(f.contribution, 4),
                    "reason": f.reason,
                }
                for f in self.factors
            },
            "redundancy_penalty": round(self.redundancy_penalty, 4),
            "transition_cost": round(self.transition_cost, 4),
            "reasons": self.reasons,
            "weight_set_id": self.weight_set_id,
            "decision_time": (self.computed_at.isoformat()
                              if self.computed_at else None),
        }


class StoryRanker:
    """Computes P(s,t) from stored facts. Deterministic and inspectable."""

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        lambda_redundancy: float = DEFAULT_LAMBDA_REDUNDANCY,
        mu_transition: float = DEFAULT_MU_TRANSITION,
        tau_seconds: float = DEFAULT_TAU_SECONDS,
    ):
        self.weights = weights or dict(DEFAULT_WEIGHTS)
        self.lambda_redundancy = lambda_redundancy
        self.mu_transition = mu_transition
        self.tau_seconds = tau_seconds

    # ── the nine factors ─────────────────────────────────

    def rank(self, facts: StoryFacts) -> RankingResult:
        now = facts.now or datetime.now(timezone.utc)
        factors = [
            self._importance(facts),
            self._novelty(facts),
            self._velocity(facts, now),
            self._evidence_quality(facts),
            self._relationship(facts),
            self._unresolved(facts),
            self._audience(facts),
            self._continuity(facts),
        ]
        score = sum(f.contribution for f in factors)
        result = RankingResult(
            story_id=facts.story_id,
            score=score,
            factors=factors,
            redundancy_penalty=self._redundancy(facts),
            transition_cost=self._transition(facts),
            reasons=self._reasons(factors, facts),
            weight_set_id=self.weight_set_id(),
            computed_at=now,
        )
        return result

    def _importance(self, f: StoryFacts) -> FactorValue:
        """§10.2. Real-world significance, not attention volume."""
        score = 0.0
        parts: List[str] = []

        if f.people_affected:
            # Log-scaled: 10 and 10,000 affected are different, but not by a
            # factor of 1000 in editorial terms.
            score += min(math.log10(max(f.people_affected, 1)) / 5.0, 1.0)
            parts.append(f"{f.people_affected} people affected")
        if f.economic_scale:
            score += min(math.log10(max(f.economic_scale, 1)) / 9.0, 1.0)
            parts.append(f"economic scale {f.economic_scale:g}")
        if f.is_irreversible:
            score += 0.5
            parts.append("irreversible decision")
        if f.has_safety_implication:
            score += 0.5
            parts.append("safety implication")
        score += min(f.geopolitical_reach, 1.0) * 0.5
        if f.geopolitical_reach:
            parts.append(f"geopolitical reach {f.geopolitical_reach:g}")

        # Attention is explicitly NOT an input here (§10.2).
        return FactorValue(
            name="importance",
            value=min(score, 2.0),
            weight=self.weights["importance"],
            reason="; ".join(parts) or "no importance signals recorded",
        )

    def _novelty(self, f: StoryFacts) -> FactorValue:
        """§10.3. How much new information changes current understanding.

        Measured against the world model, not article text: only *material*
        changes count, so restated content contributes nothing however many
        articles arrive.
        """
        material = [c for c in f.material_changes if c.is_material]
        if not material:
            return FactorValue(
                name="novelty", value=0.0, weight=self.weights["novelty"],
                reason="no material change since last coverage")

        total = sum(c.significance for c in material)
        kinds = sorted({c.kind.value for c in material})
        # Diminishing returns: the fifth correction in a batch is not five
        # times as novel as the first.
        value = min(total, 3.0) * (1.0 / (1.0 + 0.1 * len(material)))
        return FactorValue(
            name="novelty",
            value=round(value, 4),
            weight=self.weights["novelty"],
            reason=f"{len(material)} material changes: {', '.join(kinds)}",
        )

    def _velocity(self, f: StoryFacts, now: datetime) -> FactorValue:
        """§10.4. Recency-weighted material development rate.

        Uses observation time, so an investigation published today about an
        old incident is not ranked as stale (§9.3). Measured over material
        developments, never article count.
        """
        material = [c for c in f.material_changes if c.is_material]
        if not material:
            return FactorValue(
                name="velocity", value=0.0, weight=self.weights["velocity"],
                reason="no developments to weight")

        tau = self._tau_for(f.story_type)
        total = 0.0
        for change in material:
            elapsed = max((now - change.occurred_at).total_seconds(), 0.0)
            total += change.significance * math.exp(-elapsed / tau)

        return FactorValue(
            name="velocity",
            value=round(min(total, 3.0), 4),
            weight=self.weights["velocity"],
            reason=(
                f"{len(material)} developments, tau={tau:.0f}s for "
                f"{f.story_type}; decay from observation time"),
        )

    def _evidence_quality(self, f: StoryFacts) -> FactorValue:
        """§10.6. Quality and independence, not volume.

        Ownership groups are what make corroboration meaningful: ten outlets
        under one owner are one source, not ten (invariant I1).
        """
        if (f.primary_evidence_count + f.secondary_evidence_count
                + f.attributed_evidence_count) == 0:
            return FactorValue(
                name="evidence", value=0.0, weight=self.weights["evidence"],
                reason="no evidence recorded")

        score = 0.0
        score += min(f.primary_evidence_count, 3) * 0.6
        score += min(f.secondary_evidence_count, 3) * 0.25
        score += min(f.attributed_evidence_count, 3) * 0.1

        # Independence ratio from ownership groups, not outlet count.
        independence = (f.independent_source_groups
                        / max(f.total_source_groups, 1))
        score += independence * 0.8

        # Corrections and contradictions reduce eligibility, they do not
        # eliminate it.
        score -= min(f.corrected_evidence_count, 3) * 0.3
        score -= min(f.contradicted_claim_count, 3) * 0.2

        return FactorValue(
            name="evidence",
            value=round(max(score, 0.0), 4),
            weight=self.weights["evidence"],
            reason=(
                f"{f.primary_evidence_count} primary, "
                f"{f.secondary_evidence_count} secondary, "
                f"{f.attributed_evidence_count} attributed; "
                f"{f.independent_source_groups}/"
                f"{f.total_source_groups} independent source groups"
            ),
        )

    def _relationship(self, f: StoryFacts) -> FactorValue:
        """§10.5. Normalized centrality — contextual, never decisive."""
        normalized = f.degree_centrality / max(f.max_degree_in_graph, 1)
        score = min(normalized, 1.0) * 0.6
        score += min(f.high_impact_connections, 3) * 0.15
        return FactorValue(
            name="relationship",
            value=round(min(score, 1.0), 4),
            weight=self.weights["relationship"],
            reason=(
                f"degree {f.degree_centrality}/"
                f"{f.max_degree_in_graph}, "
                f"{f.high_impact_connections} high-impact connections"
            ),
        )

    def _unresolved(self, f: StoryFacts) -> FactorValue:
        """§10.8. The value of questions still worth asking."""
        if f.unresolved_question_count == 0:
            return FactorValue(
                name="unresolved", value=0.0, weight=self.weights["unresolved"],
                reason="no open questions")
        value = min(f.unresolved_question_count, 5) * 0.2 \
            * (0.5 + f.unresolved_question_materiality)
        return FactorValue(
            name="unresolved",
            value=round(min(value, 1.0), 4),
            weight=self.weights["unresolved"],
            reason=(
                f"{f.unresolved_question_count} open questions, "
                f"materiality {f.unresolved_question_materiality:g}"
            ),
        )

    def _audience(self, f: StoryFacts) -> FactorValue:
        """§10.7. Bounded, and never able to override importance or evidence.

        Enters as a capped additive term. A multiplier would let a popular
        story outrank a consequential one, which is the popularity feedback
        loop §10.7 forbids.
        """
        bounded = max(0.0, min(f.audience_relevance, 1.0))
        contribution_cap = MAX_AUDIENCE_CONTRIBUTION * self.weights["audience"]
        value = min(bounded, contribution_cap)
        return FactorValue(
            name="audience",
            value=round(value, 4),
            weight=1.0,
            reason=(
                f"audience relevance {f.audience_relevance:g}, capped at "
                f"{contribution_cap:.2f} so it cannot override importance "
                "or evidence"
            ),
        )

    def _continuity(self, f: StoryFacts) -> FactorValue:
        """§10.8. The value of continuing rather than switching."""
        if not f.is_currently_discussed:
            return FactorValue(
                name="continuity", value=0.0, weight=self.weights["continuity"],
                reason="not currently under discussion")
        return FactorValue(
            name="continuity",
            value=0.5,
            weight=self.weights["continuity"],
            reason="continuing an active discussion",
        )

    # ── penalties (§10.10) ───────────────────────────────

    def _redundancy(self, f: StoryFacts) -> float:
        """§10.9. How likely more broadcast time adds nothing."""
        penalty = 0.0
        penalty += min(f.prior_coverage_depth, 10) * 0.1
        if f.recent_segments_repeated:
            penalty += 0.5
        if not [c for c in f.material_changes if c.is_material]:
            penalty += 0.5      # nothing new to say
        if f.article_count_since_coverage > 5 and not [
                c for c in f.material_changes if c.is_material]:
            # Volume without development is the clearest saturation signal.
            penalty += 0.5
        return round(min(penalty, 2.0), 4)

    def _transition(self, f: StoryFacts) -> float:
        """§10.10. The cost of switching away from the current subject."""
        return 1.0 if not f.is_currently_discussed else 0.0

    # ── explanation ──────────────────────────────────────

    def _reasons(self, factors: List[FactorValue],
                 f: StoryFacts) -> List[str]:
        """§10.11: derived from structured features, in contribution order."""
        ranked = sorted(factors, key=lambda x: -abs(x.contribution))
        reasons = [f"{x.name}: {x.reason}" for x in ranked[:4]
                   if x.contribution > 0]
        material = [c for c in f.material_changes if c.is_material]
        if material:
            reasons.insert(0, f"{len(material)} material change(s) recorded")
        return reasons

    def _tau_for(self, story_type: str) -> float:
        """§10.4: decay varies by story type."""
        taus = {
            "emergency": 1800.0,
            "general": self.tau_seconds,
            "policy": 24 * 3600.0,
            "investigation": 72 * 3600.0,
        }
        return taus.get(story_type, self.tau_seconds)

    def weight_set_id(self) -> str:
        import hashlib
        canonical = "|".join(
            f"{k}:{v}" for k, v in sorted(self.weights.items()))
        return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def rank_stories(ranker: StoryRanker,
                 facts_list: List[StoryFacts]) -> List[RankingResult]:
    """Rank all stories, highest net value first (§10.10 greedy selection)."""
    results = [ranker.rank(f) for f in facts_list]
    results.sort(key=lambda r: -r.net_value)
    return results
