"""Independent analysis and disagreement detection.

Spec §12.4 and §12.5.

§12.4's warning is the reason this module exists: "Generating every persona
response sequentially in one unconstrained conversation can cause later
personas to inherit the assumptions and framing of earlier responses."

So the orchestrator enforces the sequence structurally. A persona's first-pass
analysis is produced from the shared evidence packet and nothing else — it
cannot see another persona's output until every first pass is complete. The
`IndependentAnalysisRunner` makes that ordering a property of the code, not a
convention a prompt might violate.

§12.5 adds the two refusals that matter: do not manufacture disagreement when
the evidence supports a straightforward conclusion, and do not force consensus
when the issue remains unresolved.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from .entities import (
    Disagreement,
    DisagreementType,
    PersonaAnalysis,
    PersonaConfig,
    ResolutionStatus,
)


@dataclass
class SharedEvidencePacket:
    """§12.4 step 1. The common baseline every persona draws on.

    §12.3: "The shared knowledge model should provide a common evidence
    baseline." Without it, personas could "disagree" about facts rather than
    about interpretation.
    """
    id: str = ""
    question: str = ""
    facts: list[str] = field(default_factory=list)
    evidence_references: list[str] = field(default_factory=list)
    contradictory_evidence: list[str] = field(default_factory=list)
    world_snapshot_id: str = ""

    @property
    def fact_set(self) -> set:
        return {f.lower().strip() for f in self.facts}


# A persona analysis function: (config, packet) -> PersonaAnalysis.
AnalysisFunction = Callable[[PersonaConfig, SharedEvidencePacket], PersonaAnalysis]


class IndependentAnalysisRunner:
    """§12.4 steps 2–4. First passes are produced in isolation.

    The ordering guarantee: `run_first_passes` completes every persona's
    analysis before any persona function is allowed to see another's result.
    Each function receives only the shared packet.
    """

    def __init__(self, analysis_functions: dict[str, AnalysisFunction] | None = None):
        self.analysis_functions = analysis_functions or {}

    def register(self, persona_id: str, function: AnalysisFunction) -> None:
        self.analysis_functions[persona_id] = function

    def run_first_passes(
        self,
        personas: list[PersonaConfig],
        packet: SharedEvidencePacket,
    ) -> list[PersonaAnalysis]:
        """Produce every first pass. None may reference another's output."""
        analyses: list[PersonaAnalysis] = []
        for persona in personas:
            function = self.analysis_functions.get(persona.id)
            if function is None:
                analyses.append(self._default_analysis(persona, packet))
                continue
            analysis = function(persona, packet)
            analysis.persona_version = persona.configuration_version
            analyses.append(analysis)
        return analyses

    def _default_analysis(
        self, persona: PersonaConfig, packet: SharedEvidencePacket
    ) -> PersonaAnalysis:
        """A persona with no registered analysis function still produces a
        recorded analysis — an empty one, honestly, rather than a fabricated one.
        """
        return PersonaAnalysis(
            persona_id=persona.id,
            persona_version=persona.configuration_version,
            question=packet.question,
            position="",
            assumptions=[],
            evidence_used=list(packet.evidence_references),
            shared_facts_accepted=list(packet.facts),
            confidence=0.0,
        )


# ── §12.5 Disagreement detection ─────────────────────────────────────


class DisagreementDetector:
    """Finds substantive disagreement between first-pass analyses.

    Deterministic by construction: it compares recorded positions, assumptions,
    and evidence use. It does not read prose for sentiment. That means it finds
    the disagreements the structured record already contains, which is exactly
    the set §12.5 asks for — a disagreement must identify a precise proposition,
    not a vibe.
    """

    # Positions that differ by less than this in normalized form are not a
    # substantive disagreement.
    MIN_POSITION_DIVERGENCE = 0.15

    def detect(
        self,
        analyses: list[PersonaAnalysis],
        packet: SharedEvidencePacket,
    ) -> list[Disagreement]:
        """Compare each pair of analyses against the shared baseline."""
        disagreements: list[Disagreement] = []
        for i, first in enumerate(analyses):
            for second in analyses[i + 1:]:
                found = self._compare(first, second, packet)
                disagreements.extend(found)
        return [d for d in disagreements if d.is_substantive]

    def _compare(
        self,
        a: PersonaAnalysis,
        b: PersonaAnalysis,
        packet: SharedEvidencePacket,
    ) -> list[Disagreement]:
        results: list[Disagreement] = []

        if not a.position or not b.position:
            return results

        # Facts both accept are shared, not disputed.
        shared = sorted(
            {f.lower() for f in a.shared_facts_accepted}
            & {f.lower() for f in b.shared_facts_accepted}
            | (packet.fact_set & {f.lower() for f in a.shared_facts_accepted}
               & {f.lower() for f in b.shared_facts_accepted})
        )

        # Assumptions only one side makes are the disputed ones.
        a_assumptions = {x.lower() for x in a.assumptions}
        b_assumptions = {x.lower() for x in b.assumptions}
        disputed = sorted(a_assumptions ^ b_assumptions)

        # Different positions on the same question, or different assumptions.
        positions_differ = self._diverge(a.position, b.position)
        if positions_differ:
            results.append(Disagreement(
                subject=packet.question,
                proposition=self._extract_proposition(a, b, packet),
                persona_a_id=a.persona_id,
                position_a=a.position,
                persona_b_id=b.persona_id,
                position_b=b.position,
                shared_facts=shared,
                disputed_assumptions=disputed,
                evidence_references=sorted(
                    set(a.evidence_used) ^ set(b.evidence_used)),
                disagreement_type=self._classify(a, b, disputed),
                resolution_status=ResolutionStatus.UNRESOLVED,
            ))
        elif disputed:
            # Same conclusion, different reasoning. Still recorded — it is a
            # difference in assumption, not a manufactured conflict.
            results.append(Disagreement(
                subject=packet.question,
                proposition="shared conclusion reached via different assumptions",
                persona_a_id=a.persona_id,
                position_a=a.position,
                persona_b_id=b.persona_id,
                position_b=b.position,
                shared_facts=shared,
                disputed_assumptions=disputed,
                evidence_references=sorted(
                    set(a.evidence_used) & set(b.evidence_used)),
                disagreement_type=DisagreementType.COMPETING_CAUSAL_EXPLANATIONS,
                resolution_status=ResolutionStatus.NOT_APPLICABLE,
            ))
        return results

    def _diverge(self, a: str, b: str) -> bool:
        """Whether two positions differ enough to be a real disagreement."""
        a_words = set(a.lower().split())
        b_words = set(b.lower().split())
        if not a_words or not b_words:
            return False
        overlap = len(a_words & b_words) / max(len(a_words | b_words), 1)
        return overlap < (1.0 - self.MIN_POSITION_DIVERGENCE)

    def _extract_proposition(
        self, a: PersonaAnalysis, b: PersonaAnalysis,
        packet: SharedEvidencePacket,
    ) -> str:
        """The precise proposition under dispute (§12.5)."""
        if packet.question:
            return packet.question
        a_words = set(a.position.lower().split())
        b_words = set(b.position.lower().split())
        common = sorted(a_words & b_words)
        return " ".join(common) if common else a.position[:80]

    def _classify(
        self, a: PersonaAnalysis, b: PersonaAnalysis,
        disputed: list[str],
    ) -> DisagreementType:
        """Classify by what actually differs.

        Different evidence on the same proposition is an evidence-assessment
        disagreement. Different assumptions with shared evidence is competing
        explanation. Same evidence, same assumptions, different conclusion is a
        value judgment or forecast.
        """
        a_evidence = set(a.evidence_used)
        b_evidence = set(b.evidence_used)
        if a_evidence != b_evidence:
            return DisagreementType.DIFFERENT_EVIDENCE_ASSESSMENTS
        if disputed:
            return DisagreementType.COMPETING_CAUSAL_EXPLANATIONS
        return DisagreementType.DIFFERENT_VALUE_JUDGMENTS


class DisagreementGuard:
    """§12.5's two refusals, as explicit checks.

    "The system should not manufacture disagreement when the evidence supports
    a straightforward conclusion. Likewise, it should not force a consensus
    when the underlying issue remains unresolved."
    """

    def should_suppress(
        self,
        packet: SharedEvidencePacket,
        analyses: list[PersonaAnalysis],
    ) -> bool:
        """True when the evidence supports one conclusion and manufactured
        disagreement would be noise.

        A straightforward conclusion: every persona accepts the same shared
        facts, cites the same evidence, **and** reaches positions that do not
        substantively diverge. The position check is primary — two personas can
        share every fact and evidence and still reach opposite conclusions,
        which is exactly the disagreement worth airing.
        """
        if len(analyses) < 2:
            return True

        positions = [a.position.strip().lower() for a in analyses if a.position]
        # No positions at all, or all identical, means nothing to debate.
        if len(set(positions)) <= 1:
            return True

        # Positions differ. Only suppress if the analyses are ALSO identical
        # in facts and evidence AND the positions do not substantively
        # diverge — an empty fact set is trivially "equal" and must not be
        # mistaken for consensus.
        fact_sets = [{f.lower() for f in a.shared_facts_accepted}
                     for a in analyses]
        evidence_sets = [set(a.evidence_used) for a in analyses]
        facts_unanimous = bool(fact_sets) and all(
            fs == fact_sets[0] for fs in fact_sets)
        evidence_unanimous = bool(evidence_sets) and all(
            es == evidence_sets[0] for es in evidence_sets)
        if not (facts_unanimous and evidence_unanimous):
            return False

        # Same facts, same evidence — but do the positions actually diverge?
        detector = DisagreementDetector()
        return not detector.detect(analyses, packet)

    def must_not_force_consensus(
        self, disagreements: list[Disagreement]
    ) -> list[Disagreement]:
        """Return the disagreements that remain unresolved.

        A disagreement is only closed by evidence. A dialogue that ends with
        every persona agreeing, when the underlying issue is unresolved, has
        manufactured consensus — the failure §12.5 names.
        """
        return [d for d in disagreements
                if d.resolution_status is ResolutionStatus.UNRESOLVED
                and d.is_substantive]
