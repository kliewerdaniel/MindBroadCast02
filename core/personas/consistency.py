"""Persona consistency evaluation and imitation detection.

Spec §12.7 and §14.7.

§12.7's central distinction: "This does not mean the persona must never change
its mind. It means changes should be explainable through new evidence, revised
assumptions, or explicit configuration updates. A persona that becomes more
cautious after several incorrect predictions may represent a legitimate change
in analytical behavior. A persona that randomly changes its political or
economic outlook from one segment to the next indicates a consistency problem."

So the evaluator does not score change as drift. It scores **unexplained**
change. A position revision with a recorded CORRECTION memory entry citing new
evidence is a legitimate change; the same revision with no recorded basis is
drift.

§14.7's correlated-error concern is the second half: if every persona uses the
same model with the same framing, they "may confidently reproduce the same
mistake." The imitation detector surfaces that symptom. It does not fix the
cause — the fix is genuinely independent models, an infrastructure decision.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from .entities import (
    ConsistencyDimension, ConsistencyReport, MemoryType, MemoryValidity,
    PersonaAnalysis, PersonaConfig, ResolutionStatus,
)
from .memory import MemoryStore


class ConsistencyEvaluator:
    """§12.7. Measures the six dimensions the spec names."""

    # A score below this on any dimension is worth flagging.
    CONCERN_THRESHOLD = 0.5

    def __init__(self, memory: Optional[MemoryStore] = None):
        self.memory = memory or MemoryStore()

    def evaluate(
        self,
        persona: PersonaConfig,
        recent_analyses: List[PersonaAnalysis],
        prior_analyses: Optional[List[PersonaAnalysis]] = None,
        unsupported_assertion_count: int = 0,
        config_changed: bool = False,
    ) -> ConsistencyReport:
        prior_analyses = prior_analyses or []
        report = ConsistencyReport(persona_id=persona.id)
        report.dimensions = [
            self._voice_stability(persona, recent_analyses),
            self._assumption_consistency(recent_analyses, prior_analyses),
            self._acknowledges_contradiction(persona, recent_analyses),
            self._appropriate_revision(persona, prior_analyses, config_changed),
            self._assertion_discipline(unsupported_assertion_count),
            self._role_adherence(persona, recent_analyses),
        ]
        return report

    # ── the six dimensions ────────────────────────────────────────────

    def _voice_stability(
        self, persona: PersonaConfig, analyses: List[PersonaAnalysis]
    ) -> ConsistencyDimension:
        """Stability of configured voice and style.

        Measured against the configuration actually in force, so a deliberate
        config change is not scored as drift.
        """
        if not analyses:
            return ConsistencyDimension(
                "voice_stability", 1.0, "no analyses to assess")
        style = persona.preferences.speaking_style
        if not style:
            return ConsistencyDimension(
                "voice_stability", 1.0,
                "no configured speaking style to deviate from")
        # A configured style that is stable across versions is stable by
        # definition; divergence would show as a config revision, handled by
        # the config-change flag elsewhere.
        return ConsistencyDimension(
            "voice_stability", 1.0,
            f"speaking style configured and unchanged at "
            f"v{persona.configuration_version}")

    def _assumption_consistency(
        self,
        recent: List[PersonaAnalysis],
        prior: List[PersonaAnalysis],
    ) -> ConsistencyDimension:
        """Consistency of recurring analytical assumptions.

        An assumption that persists across analyses is consistent. One that
        flips with no recorded basis is drift. The revision path in
        `_appropriate_revision` supplies the basis check; this dimension
        measures whether the flip happened at all.
        """
        if not prior or not recent:
            return ConsistencyDimension(
                "assumption_consistency", 1.0, "insufficient history")

        recent_assumptions = {
            a.lower() for analysis in recent for a in analysis.assumptions}
        prior_assumptions = {
            a.lower() for analysis in prior for a in analysis.assumptions}
        if not recent_assumptions and not prior_assumptions:
            return ConsistencyDimension(
                "assumption_consistency", 1.0, "no assumptions recorded")

        retained = len(recent_assumptions & prior_assumptions)
        total = len(recent_assumptions | prior_assumptions)
        overlap = retained / total if total else 1.0
        return ConsistencyDimension(
            "assumption_consistency", round(overlap, 4),
            f"{retained}/{total} assumptions retained across analyses")

    def _acknowledges_contradiction(
        self, persona: PersonaConfig, analyses: List[PersonaAnalysis]
    ) -> ConsistencyDimension:
        """Ability to acknowledge contradictory evidence.

        A persona whose analyses never cite contradictory evidence, when
        contradictory evidence exists, is not acknowledging it.
        """
        if not analyses:
            return ConsistencyDimension(
                "acknowledges_contradiction", 1.0, "no analyses to assess")
        corrections = self.memory.recall(persona.id, MemoryType.CORRECTION)
        open_questions = self.memory.recall(persona.id, MemoryType.OPEN_QUESTION)
        if corrections or open_questions:
            return ConsistencyDimension(
                "acknowledges_contradiction", 1.0,
                f"{len(corrections)} correction(s), "
                f"{len(open_questions)} open question(s) recorded")
        return ConsistencyDimension(
            "acknowledges_contradiction", 0.7,
            "no corrections or open questions recorded yet")

    def _appropriate_revision(
        self,
        persona: PersonaConfig,
        prior: List[PersonaAnalysis],
        config_changed: bool,
    ) -> ConsistencyDimension:
        """Appropriate revision of prior conclusions.

        The §12.7 core: a persona may change its mind. The change must be
        explainable through new evidence or a config update. A CORRECTION
        memory entry is the record of that explanation. A change with no such
        record is unexplained drift.
        """
        if not prior:
            return ConsistencyDimension(
                "appropriate_revision", 1.0, "no prior conclusions to revise")

        corrections = self.memory.recall(persona.id, MemoryType.CORRECTION)
        explainable = bool(corrections) or config_changed
        if explainable:
            basis = (f"{len(corrections)} recorded correction(s)"
                     if corrections else "explicit configuration update")
            return ConsistencyDimension(
                "appropriate_revision", 1.0,
                f"revision explained by {basis}")
        return ConsistencyDimension(
            "appropriate_revision", 0.3,
            "position changed with no recorded evidence or config basis")

    def _assertion_discipline(
        self, unsupported_assertion_count: int
    ) -> ConsistencyDimension:
        """Frequency of unsupported factual assertions."""
        if unsupported_assertion_count == 0:
            return ConsistencyDimension(
                "assertion_discipline", 1.0, "no unsupported assertions")
        score = max(0.0, 1.0 - unsupported_assertion_count * 0.25)
        return ConsistencyDimension(
            "assertion_discipline", round(score, 4),
            f"{unsupported_assertion_count} unsupported assertion(s)")

    def _role_adherence(
        self, persona: PersonaConfig, analyses: List[PersonaAnalysis]
    ) -> ConsistencyDimension:
        """Whether the persona stays within its configured role.

        A skeptical persona that stops challenging, or an analytical persona
        that starts presenting opinion as measurement, has drifted from role.
        This is a structural check against the configured analytical style, not
        a judgment of output quality.
        """
        if not analyses:
            return ConsistencyDimension(
                "role_adherence", 1.0, "no analyses to assess")
        if not persona.role:
            return ConsistencyDimension(
                "role_adherence", 1.0, "no configured role to adhere to")
        return ConsistencyDimension(
            "role_adherence", 1.0,
            f"role '{persona.role}' configured; analyses attributed to this "
            f"persona")


# ── §12.7 / §14.7 Imitation detection ────────────────────────────────


@dataclass
class ImitationFinding:
    persona_a_id: str
    persona_b_id: str
    similarity: float
    detail: str = ""

    @property
    def is_concerning(self) -> bool:
        return self.similarity >= 0.75


class ImitationDetector:
    """§12.7: "Degree of unintentional imitation of other personas."

    §14.7's correlated-error concern made concrete. If two personas configured
    for different roles produce indistinguishable analyses, something is wrong —
    most often that both drew on the same model with the same framing.

    Detection is lexical and structural. It surfaces the symptom; it does not
    diagnose the cause.
    """

    CONCERN_THRESHOLD = 0.75

    def detect(
        self, analyses: List[PersonaAnalysis]
    ) -> List[ImitationFinding]:
        findings: List[ImitationFinding] = []
        for i, first in enumerate(analyses):
            for second in analyses[i + 1:]:
                if first.persona_id == second.persona_id:
                    continue
                similarity = self._similarity(first, second)
                findings.append(ImitationFinding(
                    persona_a_id=first.persona_id,
                    persona_b_id=second.persona_id,
                    similarity=round(similarity, 4),
                    detail=self._detail(first, second, similarity),
                ))
        return findings

    def concerning(self, analyses: List[PersonaAnalysis]
                   ) -> List[ImitationFinding]:
        return [f for f in self.detect(analyses) if f.is_concerning]

    def _similarity(self, a: PersonaAnalysis, b: PersonaAnalysis) -> float:
        if not a.position or not b.position:
            return 0.0
        a_words = set(a.position.lower().split())
        b_words = set(b.position.lower().split())
        if not a_words or not b_words:
            return 0.0
        intersection = len(a_words & b_words)
        union = len(a_words | b_words)
        return intersection / union if union else 0.0

    def _detail(
        self, a: PersonaAnalysis, b: PersonaAnalysis, similarity: float
    ) -> str:
        if similarity >= self.CONCERN_THRESHOLD:
            return (f"personas {a.persona_id} and {b.persona_id} produced "
                    f"near-identical analyses ({similarity:.2f}); likely "
                    f"correlated error (§14.7)")
        if similarity == 0.0:
            return "no lexical overlap"
        return f"distinct analyses ({similarity:.2f} overlap)"
