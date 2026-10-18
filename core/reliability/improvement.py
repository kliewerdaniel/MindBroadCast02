"""Recursive improvement.

Spec §28. A separate subsystem, "rather than an unrestricted extension of the
Broadcast Director."

The cycle: Observe → Evaluate → Identify Failure → Propose Change → Test →
Approve → Deploy → Measure.

Two hard constraints from the spec, both enforced structurally:

§28.2: "The system should not autonomously weaken factual validation merely
because doing so increases the number of segments successfully broadcast." So
`PROTECTED_PARAMETERS` cannot be changed by the loop at all — they are outside
its authority, not merely flagged.

§28.4: evaluation "should not initially be reduced to one opaque score. The
individual dimensions should be measurable and independently inspectable." So
`QualityAssessment` reports F, P, N, C, D, R, S separately, and `is_improvement`
requires agreement across dimensions, not a single summed number.

The loop proposes and measures. It does not self-approve — §28.3 requires an
approval record, and approval is explicit.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

# §28.2: parameters the improvement loop may adjust, within bounds.
ADJUSTABLE_PARAMETERS = {
    "polling_interval_seconds": (60, 3600),
    "background_task_order": None,
    "ranking_weight_novelty": (0.0, 2.0),
    "ranking_weight_velocity": (0.0, 2.0),
    "cache_ttl_seconds": (60, 86400),
}

# §28.2: parameters the loop may NEVER touch, however tempting. Weakening
# factual validation to broadcast more is the exact failure named.
PROTECTED_PARAMETERS = (
    "validation_stages",
    "require_evidence_for_factual_claims",
    "provenance_enforcement",
    "append_only_enforcement",
    "contradiction_checking",
    "attribution_enforcement",
)


class ImprovementState(Enum):
    OBSERVED = "observed"
    FAILURE_IDENTIFIED = "failure_identified"
    CHANGE_PROPOSED = "change_proposed"
    TESTED = "tested"
    APPROVED = "approved"
    DEPLOYED = "deployed"
    REJECTED = "rejected"


@dataclass
class QualityDimension:
    """§28.4: one of the seven dimensions, independently inspectable."""
    name: str
    value: float          # 0.0 to 1.0

    @property
    def label(self) -> str:
        return {
            "F": "factual_grounding",
            "P": "provenance_integrity",
            "N": "novelty",
            "C": "clarity",
            "D": "analytical_depth",
            "R": "relevance",
            "S": "operational_stability",
        }.get(self.name, self.name)


@dataclass
class QualityAssessment:
    """§28.4. Seven dimensions, never collapsed to one score."""
    dimensions: dict[str, float] = field(default_factory=dict)

    def dimension(self, name: str) -> QualityDimension | None:
        value = self.dimensions.get(name)
        return QualityDimension(name, value) if value is not None else None

    @property
    def all_dimensions(self) -> list[QualityDimension]:
        return [QualityDimension(k, v) for k, v in self.dimensions.items()]

    def to_dict(self) -> dict[str, Any]:
        return {
            d.label: round(d.value, 4) for d in self.all_dimensions
        }


class ImprovementEvaluator:
    """§28.3/§28.4. Compares a proposed change against a baseline across
    multiple dimensions."""

    # A dimension must not regress by more than this for the change to be
    # considered an overall improvement.
    MAX_REGRESSION = 0.05

    def compare(self, baseline: QualityAssessment,
                candidate: QualityAssessment) -> dict[str, Any]:
        """Per-dimension comparison. No single opaque score."""
        improvements: dict[str, float] = {}
        regressions: dict[str, float] = {}
        for name in baseline.dimensions:
            base = baseline.dimensions[name]
            cand = candidate.dimensions.get(name, base)
            delta = cand - base
            if delta > 0:
                improvements[name] = round(delta, 4)
            elif delta < -self.MAX_REGRESSION:
                regressions[name] = round(delta, 4)
        return {
            "improvements": improvements,
            "regressions": regressions,
            "acceptable": len(regressions) == 0,
        }

    def is_improvement(self, baseline: QualityAssessment,
                       candidate: QualityAssessment) -> bool:
        """An improvement requires gains and no material regression (§28.4)."""
        comparison = self.compare(baseline, candidate)
        return comparison["acceptable"] and bool(comparison["improvements"])


@dataclass
class ImprovementProposal:
    """§28.3: every proposed improvement carries its full evaluation record."""
    id: str
    problem_statement: str
    parameter: str
    current_value: Any
    proposed_value: Any
    baseline: QualityAssessment | None = None
    candidate: QualityAssessment | None = None
    acceptance_criteria: str = ""
    regression_tests: list[str] = field(default_factory=list)
    rollback_procedure: str = ""
    state: ImprovementState = ImprovementState.OBSERVED
    approved_by: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_adjustable(self) -> bool:
        return self.parameter in ADJUSTABLE_PARAMETERS

    @property
    def is_protected(self) -> bool:
        return self.parameter in PROTECTED_PARAMETERS

    def within_bounds(self) -> bool:
        bounds = ADJUSTABLE_PARAMETERS.get(self.parameter)
        if bounds is None:
            return True
        low, high = bounds
        try:
            return low <= float(self.proposed_value) <= high
        except (TypeError, ValueError):
            return False


class RecursiveImprovementEngine:
    """§28. The bounded improvement loop.

    Proposes and measures. Approval is explicit and recorded; the engine never
    self-approves (§28.3). Protected parameters are refused outright (§28.2).
    """

    def __init__(self, evaluator: ImprovementEvaluator | None = None):
        self.evaluator = evaluator or ImprovementEvaluator()
        self.proposals: list[ImprovementProposal] = []
        self.deployment_log: list[dict[str, Any]] = []

    def propose(
        self,
        problem_statement: str,
        parameter: str,
        current_value: Any,
        proposed_value: Any,
        acceptance_criteria: str = "",
        regression_tests: list[str] | None = None,
        rollback_procedure: str = "",
    ) -> ImprovementProposal:
        """Create a proposal. Refuses protected parameters (§28.2)."""
        proposal = ImprovementProposal(
            id=f"imp-{len(self.proposals) + 1:04d}",
            problem_statement=problem_statement,
            parameter=parameter,
            current_value=current_value,
            proposed_value=proposed_value,
            acceptance_criteria=acceptance_criteria,
            regression_tests=list(regression_tests or []),
            rollback_procedure=rollback_procedure,
        )

        if proposal.is_protected:
            proposal.state = ImprovementState.REJECTED
            self.proposals.append(proposal)
            return proposal

        if not proposal.is_adjustable:
            proposal.state = ImprovementState.REJECTED
            self.proposals.append(proposal)
            return proposal

        if not proposal.within_bounds():
            proposal.state = ImprovementState.REJECTED
            self.proposals.append(proposal)
            return proposal

        proposal.state = ImprovementState.CHANGE_PROPOSED
        self.proposals.append(proposal)
        return proposal

    def evaluate(self, proposal: ImprovementProposal,
                 baseline: QualityAssessment,
                 candidate: QualityAssessment) -> bool:
        """§28.3: evaluate against a baseline across multiple dimensions."""
        proposal.baseline = baseline
        proposal.candidate = candidate
        proposal.state = ImprovementState.TESTED
        return self.evaluator.is_improvement(baseline, candidate)

    def approve(self, proposal: ImprovementProposal,
                approver: str) -> bool:
        """§28.3: an explicit approval record. The engine never self-approves."""
        if proposal.state is not ImprovementState.TESTED:
            return False
        if proposal.is_protected:
            return False
        proposal.approved_by = approver
        proposal.state = ImprovementState.APPROVED
        return True

    def deploy(self, proposal: ImprovementProposal) -> bool:
        """§28.3: deploy only an approved proposal, with a rollback path."""
        if proposal.state is not ImprovementState.APPROVED:
            return False
        if not proposal.rollback_procedure:
            # §28.3 requires a rollback procedure before deployment.
            return False
        proposal.state = ImprovementState.DEPLOYED
        self.deployment_log.append({
            "proposal_id": proposal.id,
            "parameter": proposal.parameter,
            "from": proposal.current_value,
            "to": proposal.proposed_value,
            "approved_by": proposal.approved_by,
            "rollback": proposal.rollback_procedure,
            "deployed_at": datetime.now(UTC).isoformat(),
        })
        return True

    def cannot_weaken_validation(self, proposal: ImprovementProposal) -> bool:
        """§28.2's hard rule, as a checkable property."""
        return proposal.is_protected
