"""Pass 7: Contradiction and corroboration analysis.

Spec §8.2 is explicit that this is the pass most likely to produce false
positives:

    Not all conflicting language indicates a contradiction. For example, two
    sources may report different unemployment rates because they refer to
    different months or use different measures. The system should attempt to
    identify those distinctions before marking the claims as contradictory.

So the analysis compares *quantity + unit + temporal qualifier*, not sentence
similarity. Two claims with the same unit and the same temporal scope but
different values are `CONTRADICTS`. If the temporal scope differs, the
difference is a definitional or measurement distinction — `QUALIFIES`, not a
contradiction. Flagging it as a contradiction would be a false allegation
presented as a finding, which is exactly what invariant I10 forbids.
"""

from dataclasses import dataclass, field
from enum import Enum

from ..models import EvidenceRelationship
from .claims import ExtractedClaim


class AnalysisOutcome(Enum):
    CORROBORATION = "corroboration"
    DIRECT_CONTRADICTION = "direct_contradiction"
    MEASUREMENT_DIFFERENCE = "measurement_difference"
    TEMPORAL_DIFFERENCE = "temporal_difference"
    DEFINITION_DIFFERENCE = "definition_difference"
    ATTRIBUTION_DIFFERENCE = "attribution_difference"
    STALE_INFORMATION = "stale_information"
    NO_RELATION = "no_relation"


# Units that measure the same thing and are therefore comparable. A percentage
# and a percentage point are not the same unit.
_COMPARABLE_UNITS = [
    {"%", "percent", "per cent", "percentage"},
    {"million", "billion", "trillion"},     # same scale, different magnitude
    {"people", "persons", "deaths", "cases"},
    {"dollars", "euros", "pounds"},
]

_SCALE = {"million": 1e6, "billion": 1e9, "trillion": 1e12}


@dataclass
class ContradictionFinding:
    outcome: AnalysisOutcome
    new_claim_index: int
    existing_claim_id: str | None = None
    relationship: EvidenceRelationship | None = None
    basis: str = ""
    confidence: float = 0.0
    new_value: str | None = None
    existing_value: str | None = None
    new_temporal: str | None = None
    existing_temporal: str | None = None

    @property
    def is_contradiction(self) -> bool:
        return self.outcome is AnalysisOutcome.DIRECT_CONTRADICTION


@dataclass
class ContradictionReport:
    findings: list[ContradictionFinding] = field(default_factory=list)
    pass_name: str = "contradiction_analysis"
    pass_version: str = "1.0.0"
    warnings: list[str] = field(default_factory=list)

    @property
    def contradictions(self) -> list[ContradictionFinding]:
        return [f for f in self.findings if f.is_contradiction]

    @property
    def corroborations(self) -> list[ContradictionFinding]:
        return [f for f in self.findings
                if f.outcome is AnalysisOutcome.CORROBORATION]


@dataclass
class ExistingClaimView:
    """The minimum the analysis needs about a stored claim."""
    claim_id: str
    canonical_proposition: str = ""
    claim_type: str | None = None
    quantities: list[tuple[str, str | None]] = field(default_factory=list)
    temporals: list[str] = field(default_factory=list)
    attributed_to: str | None = None
    is_causal: bool = False


class ContradictionAnalyzer:
    """Pass 7. Compares qualifiers before declaring a contradiction."""

    name = "contradiction_analysis"
    version = "1.0.0"

    def run(
        self,
        new_claims: list[ExtractedClaim],
        existing: list[ExistingClaimView],
    ) -> ContradictionReport:
        report = ContradictionReport()
        for index, claim in enumerate(new_claims):
            for candidate in self._comparable_existing(claim, existing):
                finding = self._compare(claim, candidate, index)
                if finding is not None:
                    report.findings.append(finding)
        return report

    # ── candidate selection ──────────────────────────────

    def _comparable_existing(
        self, claim: ExtractedClaim, existing: list[ExistingClaimView]
    ) -> list[ExistingClaimView]:
        """Only claims that could bear on the same proposition.

        Comparing everything against everything would manufacture
        contradictions between unrelated reporting, which is worse than
        missing one.
        """
        comparable: list[ExistingClaimView] = []
        claim_units = {self._normalize_unit(q.unit) for q in claim.quantities}
        for candidate in existing:
            if not candidate.quantities and claim.quantities:
                continue
            candidate_units = {
                self._normalize_unit(u) for _, u in candidate.quantities}
            if (claim_units and candidate_units
                    and not self._units_overlap(claim_units, candidate_units)):
                continue
            if self._shares_subject(claim, candidate):
                comparable.append(candidate)
        return comparable

    def _shares_subject(self, claim: ExtractedClaim,
                        candidate: ExistingClaimView) -> bool:
        """Rough subject overlap: shared significant words.

        The threshold is 1, not 2. Two reports about "unemployment" share
        exactly one significant word once the values and months are stripped
        ("rose" vs "was"), and those are precisely the claims Pass 7 exists to
        compare. Requiring 2 would filter out the canonical contradiction case
        and silently produce an empty report instead of a finding.
        """
        new_words = self._significant(claim.canonical_proposition)
        old_words = self._significant(candidate.canonical_proposition)
        if not new_words or not old_words:
            return False
        return len(new_words & old_words) >= 1

    @staticmethod
    def _significant(text: str) -> set:
        stop = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
            "of", "for", "with", "by", "from", "that", "this", "said", "says",
            "had", "has", "have", "was", "were", "been", "being", "is", "are",
            "it", "its", "as", "which", "who", "after", "before",
        }
        return {
            w.lower().strip(".,;:!?\"'") for w in text.split()
            if w.lower().strip(".,;:!?\"'") not in stop and len(w) > 3
        }

    # ── comparison ───────────────────────────────────────

    def _compare(
        self, claim: ExtractedClaim, candidate: ExistingClaimView, index: int
    ) -> ContradictionFinding | None:
        new_q = claim.quantities[0] if claim.quantities else None
        old_q = (candidate.quantities[0] if candidate.quantities else None)
        # `new_q` is a Quantity (attribute access); `old_q` is a
        # (value, unit) tuple from the stored view. Normalise both to a
        # small named pair so the comparisons below read the same way.
        if old_q is not None:
            old_value, old_unit = old_q
        else:
            old_value, old_unit = None, None

        if new_q is None or old_q is None:
            return self._compare_without_quantities(claim, candidate, index)

        new_temporal = self._temporal(claim)
        old_temporal = self._temporal_of(candidate)
        new_unit = self._normalize_unit(new_q.unit)
        old_unit = self._normalize_unit(old_unit)

        # Same unit, same temporal scope, different value -> contradiction.
        if self._units_overlap({new_unit}, {old_unit}):
            if new_temporal and old_temporal and new_temporal != old_temporal:
                return ContradictionFinding(
                    outcome=AnalysisOutcome.TEMPORAL_DIFFERENCE,
                    new_claim_index=index,
                    existing_claim_id=candidate.claim_id,
                    relationship=EvidenceRelationship.QUALIFIES,
                    basis=(
                        f"values differ ({new_q.value} vs {old_value}) but "
                        f"temporal scope differs ({new_temporal!r} vs "
                        f"{old_temporal!r}); not a contradiction"
                    ),
                    confidence=0.6,
                    new_value=new_q.value, existing_value=old_value,
                    new_temporal=new_temporal, existing_temporal=old_temporal,
                )

            new_value = self._numeric(new_q.value)
            old_value = self._numeric(old_value)
            if new_value is not None and old_value is not None:
                if self._same_value(new_value, old_value, new_unit, old_unit):
                    return ContradictionFinding(
                        outcome=AnalysisOutcome.CORROBORATION,
                        new_claim_index=index,
                        existing_claim_id=candidate.claim_id,
                        relationship=EvidenceRelationship.SUPPORTS,
                        basis=(
                            f"same value ({new_q.value}) in the same scope; "
                            "independent restatement"
                        ),
                        confidence=0.7,
                        new_value=new_q.value, existing_value=old_value,
                    )
                return ContradictionFinding(
                    outcome=AnalysisOutcome.DIRECT_CONTRADICTION,
                    new_claim_index=index,
                    existing_claim_id=candidate.claim_id,
                    relationship=EvidenceRelationship.CONTRADICTS,
                    basis=(
                        f"same unit and scope, different values "
                        f"({new_q.value} vs {old_value})"
                    ),
                    confidence=0.8,
                    new_value=new_q.value, existing_value=old_value,
                    new_temporal=new_temporal, existing_temporal=old_temporal,
                )

        # Different units measuring the same kind of thing.
        if (self._units_overlap({new_unit}, {old_unit}) is False
                and self._same_measure_family(new_unit, old_unit)):
                return ContradictionFinding(
                    outcome=AnalysisOutcome.MEASUREMENT_DIFFERENCE,
                    new_claim_index=index,
                    existing_claim_id=candidate.claim_id,
                    relationship=EvidenceRelationship.QUALIFIES,
                    basis=(
                        f"different units in the same measure family "
                        f"({new_unit} vs {old_unit}); may be a definitional "
                        "difference rather than a conflict"
                    ),
                    confidence=0.5,
                    new_value=new_q.value, existing_value=old_value,
                )
        return None

    def _compare_without_quantities(
        self, claim: ExtractedClaim, candidate: ExistingClaimView, index: int
    ) -> ContradictionFinding | None:
        if (claim.is_attributed and candidate.attributed_to
                and claim.attributed_to and candidate.attributed_to
                and (claim.attributed_to.lower()
                     != candidate.attributed_to.lower())):
                    return ContradictionFinding(
                        outcome=AnalysisOutcome.ATTRIBUTION_DIFFERENCE,
                        new_claim_index=index,
                        existing_claim_id=candidate.claim_id,
                        relationship=EvidenceRelationship.PROVIDES_CONTEXT,
                        basis=(
                            "different attributed sources; a disagreement "
                            "between speakers is not a factual contradiction"
                        ),
                        confidence=0.4,
                    )
        return None

    # ── unit and value helpers ───────────────────────────

    @staticmethod
    def _normalize_unit(unit: str | None) -> str:
        if not unit:
            return ""
        text = unit.lower().strip()
        text = text.replace("percentage points", "pp")
        text = text.replace("percentage point", "pp")
        if text in ("per cent", "percent", "percentage"):
            return "%"
        return text

    def _units_overlap(self, a: set, b: set) -> bool:
        a = {u for u in a if u}
        b = {u for u in b if u}
        if not a or not b:
            return False
        if a & b:
            return True
        for family in _COMPARABLE_UNITS:
            normalized = {self._normalize_unit(u) for u in family}
            if a & normalized and b & normalized:
                return True
        return False

    def _same_measure_family(self, a: str, b: str) -> bool:
        if not a or not b:
            return False
        for family in _COMPARABLE_UNITS:
            normalized = {self._normalize_unit(u) for u in family}
            if a in normalized and b in normalized:
                return True
        return False

    @staticmethod
    def _numeric(value: str) -> float | None:
        try:
            return float(value.replace(",", ""))
        except (ValueError, AttributeError):
            return None

    def _same_value(self, new: float, old: float,
                    new_unit: str, old_unit: str) -> bool:
        """Values equal, accounting for magnitude units (million vs billion)."""
        new_scaled = new * _SCALE.get(new_unit, 1.0)
        old_scaled = old * _SCALE.get(old_unit, 1.0)
        if new_scaled == old_scaled:
            return True
        # Exact equality is the bar; a 0.1% difference in a reported figure
        # is not corroboration, it is rounding, and calling it either is a
        # judgement the system should not make silently.
        return False

    @staticmethod
    def _temporal(claim: ExtractedClaim) -> str | None:
        if claim.temporal_qualifiers:
            return claim.temporal_qualifiers[0].phrase.lower()
        return None

    @staticmethod
    def _temporal_of(candidate: ExistingClaimView) -> str | None:
        if candidate.temporals:
            return candidate.temporals[0].lower()
        return None
