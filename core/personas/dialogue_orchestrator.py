"""Multi-persona dialogue orchestration.

Spec §12.4. The recommended sequence, enforced as code:

1. Construct a shared evidence packet.
2. Identify the question under discussion.
3. Generate an initial analysis for each assigned persona.
4. Record the assumptions and evidence used by each analysis.
5. Identify substantive disagreements.
6. Generate a structured exchange addressing those disagreements.
7. Produce a final segment script.
8. Validate the script against the shared evidence packet.

Step 3 is the load-bearing one. The runner completes every persona's
first-pass analysis **before** any exchange is composed, so no persona can
inherit another's framing. That ordering is a property of this orchestrator,
not a convention a prompt might violate.
"""

from dataclasses import dataclass, field
from typing import Any

from ..broadcast.entities import (
    BroadcastScript,
    ScriptTurn,
    SpeechAct,
)
from ..broadcast.validation import ScriptValidator
from .analysis import (
    AnalysisFunction,
    DisagreementDetector,
    DisagreementGuard,
    IndependentAnalysisRunner,
    SharedEvidencePacket,
)
from .dialogue import DialogueComposer
from .entities import (
    Disagreement,
    MemoryType,
    PersonaAnalysis,
    StructuredExchange,
)
from .memory import MemoryStore, PersonaRegistry


@dataclass
class DialogueOutcome:
    packet: SharedEvidencePacket | None = None
    analyses: list[PersonaAnalysis] = field(default_factory=list)
    disagreements: list[Disagreement] = field(default_factory=list)
    exchange: StructuredExchange | None = None
    script: BroadcastScript | None = None
    validation: Any | None = None
    suppressed: bool = False
    suppression_reason: str = ""

    @property
    def succeeded(self) -> bool:
        return (self.script is not None
                and self.validation is not None
                and self.validation.is_approved)


class DialogueOrchestrator:
    """Runs the §12.4 sequence end to end."""

    def __init__(
        self,
        registry: PersonaRegistry,
        memory: MemoryStore | None = None,
        runner: IndependentAnalysisRunner | None = None,
        validator: ScriptValidator | None = None,
        composer: DialogueComposer | None = None,
    ):
        self.registry = registry
        self.memory = memory or MemoryStore()
        self.runner = runner or IndependentAnalysisRunner()
        self.validator = validator or ScriptValidator()
        self.composer = composer or DialogueComposer()
        self.detector = DisagreementDetector()
        self.guard = DisagreementGuard()

    def register_analysis(
        self, persona_id: str, function: AnalysisFunction
    ) -> None:
        self.runner.register(persona_id, function)

    def run(
        self,
        packet: SharedEvidencePacket,
        persona_ids: list[str],
    ) -> DialogueOutcome:
        """Execute steps 1–8 of §12.4."""
        outcome = DialogueOutcome(packet=packet)

        personas = [self.registry.get(pid) for pid in persona_ids]
        personas = [p for p in personas if p is not None and p.enabled]
        if len(personas) < 2:
            outcome.suppressed = True
            outcome.suppression_reason = (
                "a multi-persona exchange requires at least two enabled "
                "personas")
            return outcome

        # Step 3: independent first passes. Completed before anything else.
        outcome.analyses = self.runner.run_first_passes(personas, packet)

        # §12.5 refusal one: do not manufacture disagreement.
        if self.guard.should_suppress(packet, outcome.analyses):
            outcome.suppressed = True
            outcome.suppression_reason = (
                "evidence supports a straightforward conclusion; "
                "manufacturing disagreement would be noise")
            return outcome

        # Step 5: identify substantive disagreements.
        outcome.disagreements = self.detector.detect(outcome.analyses, packet)

        # Step 6: structured exchange.
        outcome.exchange = self.composer.compose(
            packet, outcome.analyses, outcome.disagreements, personas)

        # Step 7: final segment script.
        outcome.script = self._to_script(outcome.exchange, packet)

        # Step 8: validate against the shared evidence packet.
        outcome.validation = self.validator.validate(outcome.script)
        return outcome

    def _to_script(
        self, exchange: StructuredExchange, packet: SharedEvidencePacket
    ) -> BroadcastScript:
        """Convert the exchange into a script for validation.

        The exchange turns are interpretive speech, not factual reports, so
        they do not require claim references — the evidence lives in the packet
        the exchange was built from. Transition framing is added as an explicit
        transition turn so the segment opens by stating the shared facts.
        """
        turns: list[ScriptTurn] = []

        if packet.facts:
            turns.append(ScriptTurn(
                sequence=0,
                persona_id=exchange.personas[0] if exchange.personas else "",
                text="What we can establish: " + "; ".join(packet.facts[:3]),
                speech_act=SpeechAct.TRANSITION,
            ))

        for index, dialogue_turn in enumerate(exchange.turns, start=1):
            turns.append(ScriptTurn(
                sequence=index,
                persona_id=dialogue_turn.persona_id,
                text=dialogue_turn.text,
                speech_act=self._speech_act(dialogue_turn.speech_act),
                evidence_references=list(packet.evidence_references),
                uncertainty_markers=(
                    ["unresolved"] if exchange.unresolved else []),
            ))

        if exchange.unresolved:
            turns.append(ScriptTurn(
                sequence=len(turns) + 1,
                persona_id=exchange.personas[0] if exchange.personas else "",
                text="This remains unresolved: " + "; ".join(
                    exchange.unresolved[:2]),
                speech_act=SpeechAct.TRANSITION,
            ))

        return BroadcastScript(
            segment_id=f"dialogue-{exchange.id[:12]}",
            version=1,
            evidence_packet_id=packet.id,
            turns=turns,
            world_snapshot_id=packet.world_snapshot_id,
        )

    @staticmethod
    def _speech_act(act: str) -> SpeechAct:
        try:
            return SpeechAct(act)
        except ValueError:
            return SpeechAct.INTERPRETATION

    # ── episodic memory of participation ──────────────────────────────

    def record_participation(self, outcome: DialogueOutcome,
                             segment_id: str) -> None:
        """§12.6: a record of previous participation.

        Each persona remembers it took part, and records the disagreement it
        raised as OPEN_QUESTION memory rather than burying it in episodic
        detail.
        """
        if outcome.exchange is None or outcome.packet is None:
            return
        for persona_id in outcome.exchange.personas:
            self.memory.remember(
                persona_id=persona_id,
                memory_type=MemoryType.EPISODIC,
                content=(f"Participated in a discussion of "
                         f"{outcome.packet.question[:80]}"),
                source_segment_id=segment_id,
                provenance="dialogue_orchestrator",
            )
            for disagreement_id in outcome.exchange.disagreements_addressed:
                self.memory.remember(
                    persona_id=persona_id,
                    memory_type=MemoryType.OPEN_QUESTION,
                    content=(f"Raised a disagreement (id {disagreement_id}) "
                             f"on {outcome.packet.question[:60]}"),
                    source_segment_id=segment_id,
                    provenance="dialogue_orchestrator",
                )
