"""Structured exchange composition.

Spec §12.4 step 6: "Generate a structured exchange addressing those
disagreements."

The exchange is built from the recorded disagreement records. It is not
produced by re-prompting personas until they disagree — that would manufacture
conflict, which §12.5 forbids. Each turn is traceable to the disagreement it
addresses, and the exchange carries the shared facts and the unresolved items
so the audience can see what is settled and what is not.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .entities import (
    Disagreement, DialogueTurn, PersonaConfig, PersonaAnalysis,
    ResolutionStatus, StructuredExchange,
)
from .analysis import SharedEvidencePacket


class DialogueComposer:
    """Builds a structured exchange from recorded disagreements."""

    def __init__(self, max_turns: int = 12):
        self.max_turns = max_turns

    def compose(
        self,
        packet: SharedEvidencePacket,
        analyses: List[PersonaAnalysis],
        disagreements: List[Disagreement],
        personas: Optional[List[PersonaConfig]] = None,
    ) -> StructuredExchange:
        """Address each substantive disagreement in turn.

        A disagreement with only one party (an unpaired analysis) still gets a
        turn, but it is not dressed up as a debate.
        """
        exchange = StructuredExchange(
            question=packet.question,
            shared_facts=list(packet.facts),
        )

        substantive = [d for d in disagreements if d.is_substantive]
        by_persona: Dict[str, PersonaAnalysis] = {
            a.persona_id: a for a in analyses}

        # A persona's position airs once, even if it appears in several
        # pairwise disagreements — otherwise the same sentence is spoken two or
        # three times as the debate is read out. The disagreement is still
        # recorded (disagreements_addressed); only the spoken turn is deduped.
        spoken: set = set()

        for disagreement in substantive:
            if len(exchange.turns) >= self.max_turns:
                break
            exchange.disagreements_addressed.append(disagreement.id)

            for pid, text in (
                (disagreement.persona_a_id, disagreement.position_a),
                (disagreement.persona_b_id, disagreement.position_b),
            ):
                key = (pid, text.strip())
                if not text.strip() or key in spoken:
                    continue
                spoken.add(key)
                if len(exchange.turns) >= self.max_turns:
                    break
                exchange.turns.append(DialogueTurn(
                    persona_id=pid,
                    text=text,
                    addresses_disagreement_id=disagreement.id,
                    speech_act="position",
                ))

        # Personas with no recorded disagreement still contributed an analysis.
        # They appear so the exchange is not silently narrowed to the debaters.
        addressed = {t.persona_id for t in exchange.turns}
        for analysis in analyses:
            if analysis.persona_id in addressed:
                continue
            if len(exchange.turns) >= self.max_turns:
                break
            if not analysis.position:
                continue
            exchange.turns.append(DialogueTurn(
                persona_id=analysis.persona_id,
                text=analysis.position,
                speech_act="contribution",
            ))

        # §12.5: unresolved stays unresolved. Deduped — several pairwise
        # disagreements often share one proposition (the question under
        # debate), and listing it twice reads as a stutter on air.
        seen_unresolved: set = set()
        for d in substantive:
            if (d.resolution_status is ResolutionStatus.UNRESOLVED
                    and d.proposition not in seen_unresolved):
                seen_unresolved.add(d.proposition)
                exchange.unresolved.append(d.proposition)
        return exchange

    def resolve(
        self,
        disagreement: Disagreement,
        status: ResolutionStatus,
        evidence: Optional[List[str]] = None,
    ) -> Disagreement:
        """Close a disagreement only with a stated basis.

        There is no "resolve by assertion" path. `resolved_by_convention`
        exists for editorial decisions (which side to lead with), not for
        factual ones.
        """
        disagreement.resolution_status = status
        if evidence:
            disagreement.evidence_references = sorted(
                set(disagreement.evidence_references) | set(evidence))
        return disagreement
