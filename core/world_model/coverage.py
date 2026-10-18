"""Coverage debt and saturation.

Spec §10.8 defines coverage debt as "the difference between the importance of
a story and the depth of attention the broadcaster has given it." A major event
that received only a brief announcement leaves the audience without context;
another may have received ten repetitive segments without adding understanding.

§10.9: "Saturation must not permanently suppress a story. New evidence or a
material change should reduce the saturation penalty."

That last sentence is the property most easily lost. A naive saturation
implementation monotonically suppresses a story and never lets it back, so a
correction on an old story would never be aired. The `lift` term here exists
specifically to prevent that, and it is tested directly.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class CoverageFacts:
    story_id: str
    importance: float = 0.0
    prior_coverage_depth: int = 0          # segments aired
    last_substantial_coverage_at: datetime | None = None
    unresolved_question_count: int = 0
    missing_historical_context: bool = False
    unexamined_consequences: bool = False
    corrections_needing_clarification: int = 0
    has_material_change: bool = False
    recent_segments_repeated: bool = False
    now: datetime | None = None


@dataclass
class CoverageAssessment:
    story_id: str
    debt: float
    saturation: float
    net_eligibility: float
    reasons: list[str] = field(default_factory=list)

    @property
    def is_saturated(self) -> bool:
        return self.saturation > 0.7


class CoverageModel:
    """Coverage debt (§10.8) and saturation (§10.9)."""

    # Debt accrues with time since substantive explanation.
    DEBT_TIME_TAU_HOURS = 48.0

    def assess(self, facts: CoverageFacts) -> CoverageAssessment:
        now = facts.now or datetime.now(UTC)
        debt = self._debt(facts, now)
        saturation = self._saturation(facts)
        reasons: list[str] = []

        if debt > 0:
            reasons.append(f"coverage debt {debt:.2f}")
        if saturation > 0:
            reasons.append(f"saturation {saturation:.2f}")
        if facts.has_material_change:
            reasons.append("material change lifts saturation")

        return CoverageAssessment(
            story_id=facts.story_id,
            debt=round(debt, 4),
            saturation=round(saturation, 4),
            # Debt raises eligibility; saturation lowers it; a material change
            # explicitly reduces saturation (§10.9).
            net_eligibility=round(debt - saturation, 4),
            reasons=reasons,
        )

    # ── debt (§10.8) ─────────────────────────────────────

    def _debt(self, facts: CoverageFacts, now: datetime) -> float:
        import math
        debt = 0.0

        # Importance never covered, or covered only shallowly.
        expected_depth = max(facts.importance, 0.0)
        shortfall = max(expected_depth - facts.prior_coverage_depth * 0.25, 0.0)
        debt += shortfall

        # Time since the last substantive explanation.
        if facts.last_substantial_coverage_at is not None:
            elapsed_hours = max(
                (now - facts.last_substantial_coverage_at).total_seconds()
                / 3600.0, 0.0)
            debt += 1.0 - math.exp(-elapsed_hours / self.DEBT_TIME_TAU_HOURS)
        else:
            debt += 1.0          # never substantively covered

        debt += min(facts.unresolved_question_count, 5) * 0.2
        if facts.missing_historical_context:
            debt += 0.5
        if facts.unexamined_consequences:
            debt += 0.5
        debt += min(facts.corrections_needing_clarification, 3) * 0.4
        return min(debt, 4.0)

    # ── saturation (§10.9) ───────────────────────────────

    def _saturation(self, facts: CoverageFacts) -> float:
        saturation = 0.0

        saturation += min(facts.prior_coverage_depth, 10) * 0.08
        if facts.recent_segments_repeated:
            saturation += 0.4
        if facts.unresolved_question_count == 0:
            saturation += 0.2      # nothing left to ask

        # §10.9: a material change reduces the penalty rather than being
        # overridden by it. Without this, a correction on an old story would
        # never be aired because saturation had permanently suppressed it.
        if facts.has_material_change:
            saturation = max(saturation - 0.6, 0.0)

        return min(saturation, 1.0)
