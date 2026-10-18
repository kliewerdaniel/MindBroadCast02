"""SHOW ANALYSIS — the audit surface.

Spec §18.4: "one of the most important features of MindBroadcast. It exposes
the editorial and evidentiary basis for the current broadcast."

The constraint that defines this module: "The system should not present private
chain-of-thought reasoning as an explanation. Instead, it should expose the
evidence, structured decision factors, recorded assumptions, validation
outcomes, and concise rationales needed to audit the result."

So every field is a structured, recorded artifact — never a model's internal
deliberation. `chain_of_thought` is not a field. The `redact()` method exists
to guarantee that: if a reasoning blob somehow arrives in a payload, it is
stripped before display.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..broadcast.entities import BroadcastScript
from ..broadcast.validation import ValidationReport
from ..world_model.ranking import RankingResult

# Keys that must never reach the analysis view. §18.4 forbids presenting
# private chain-of-thought as explanation.
FORBIDDEN_KEYS = (
    "chain_of_thought", "reasoning", "reasoning_trace", "thinking",
    "internal_monologue", "scratchpad", "cot", "raw_prompt",
    "system_prompt", "model_instructions",
)


@dataclass
class SelectionFactor:
    """§10.11 structured factor, surfaced for audit (§18.4 selection factors)."""
    name: str
    value: float
    contribution: float
    reason: str


@dataclass
class ShowAnalysis:
    """The ten §18.4 fields for the current segment."""
    segment_id: str = ""

    # 1. Editorial objective
    editorial_objective: str = ""

    # 2. Story context
    story_context: str = ""
    story_id: str = ""

    # 3. Evidence
    evidence: list[dict[str, Any]] = field(default_factory=list)

    # 4. Contradictions
    contradictions: list[dict[str, Any]] = field(default_factory=list)

    # 5. Interpretation
    interpretations: list[dict[str, Any]] = field(default_factory=list)

    # 6. Persona roles
    persona_roles: list[dict[str, Any]] = field(default_factory=list)

    # 7. Unresolved questions
    unresolved_questions: list[str] = field(default_factory=list)

    # 8. Selection factors
    selection_factors: list[SelectionFactor] = field(default_factory=list)

    # 9. Generation provenance
    generation_provenance: dict[str, Any] = field(default_factory=dict)

    # 10. Validation state
    validation_state: str = "unknown"
    validation_findings: list[dict[str, Any]] = field(default_factory=list)

    generated_at: datetime | None = None

    @property
    def is_complete(self) -> bool:
        """Whether all ten fields are populated enough to audit."""
        return bool(
            self.segment_id
            and self.editorial_objective
            and self.generation_provenance
            and self.validation_state != "unknown"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "editorial_objective": self.editorial_objective,
            "story_context": {
                "story_id": self.story_id,
                "summary": self.story_context,
            },
            "evidence": self.evidence,
            "contradictions": self.contradictions,
            "interpretations": self.interpretations,
            "persona_roles": self.persona_roles,
            "unresolved_questions": self.unresolved_questions,
            "selection_factors": [
                {"name": f.name, "value": f.value,
                 "contribution": f.contribution, "reason": f.reason}
                for f in self.selection_factors
            ],
            "generation_provenance": self.generation_provenance,
            "validation_state": self.validation_state,
            "validation_findings": self.validation_findings,
        }

    @classmethod
    def redact(cls, analysis: "ShowAnalysis") -> "ShowAnalysis":
        """Strip any forbidden reasoning fields from every nested payload.

        A belt-and-braces guarantee: even if a caller passes a payload
        containing model reasoning, it never reaches the audit view.
        """
        def clean(payload: Any) -> Any:
            if isinstance(payload, dict):
                return {k: clean(v) for k, v in payload.items()
                        if k.lower() not in FORBIDDEN_KEYS}
            if isinstance(payload, list):
                return [clean(v) for v in payload]
            return payload

        analysis.evidence = clean(analysis.evidence)
        analysis.contradictions = clean(analysis.contradictions)
        analysis.interpretations = clean(analysis.interpretations)
        analysis.persona_roles = clean(analysis.persona_roles)
        analysis.generation_provenance = clean(analysis.generation_provenance)
        return analysis


class ShowAnalysisBuilder:
    """Assembles the §18.4 view from stored records."""

    def build(
        self,
        segment_id: str,
        editorial_objective: str = "",
        story_id: str = "",
        story_context: str = "",
        evidence: list[dict[str, Any]] | None = None,
        contradictions: list[dict[str, Any]] | None = None,
        script: BroadcastScript | None = None,
        persona_roles: list[dict[str, Any]] | None = None,
        unresolved_questions: list[str] | None = None,
        ranking: RankingResult | None = None,
        validation: ValidationReport | None = None,
    ) -> ShowAnalysis:
        analysis = ShowAnalysis(
            segment_id=segment_id,
            editorial_objective=editorial_objective,
            story_id=story_id,
            story_context=story_context,
            evidence=list(evidence or []),
            contradictions=list(contradictions or []),
            unresolved_questions=list(unresolved_questions or []),
            generated_at=datetime.now(UTC),
        )

        # Interpretations: the script's non-factual turns (§18.4 field 5).
        if script is not None:
            analysis.interpretations = [
                {
                    "turn": t.sequence,
                    "speech_act": t.speech_act.value,
                    "epistemic_function": (
                        t.epistemic_function.value
                        if t.epistemic_function else None),
                    "text": t.text,
                }
                for t in script.turns
                if t.speech_act.value != "factual_report"
            ]
            analysis.generation_provenance = self._provenance(script)

        analysis.persona_roles = list(persona_roles or [])

        # Selection factors from the ranking decision record (§10.11).
        if ranking is not None:
            analysis.selection_factors = [
                SelectionFactor(
                    name=f.name, value=f.value,
                    contribution=round(f.contribution, 4),
                    reason=f.reason,
                )
                for f in ranking.factors
            ]

        # Validation state (§18.4 field 10).
        if validation is not None:
            analysis.validation_state = validation.outcome.value
            analysis.validation_findings = [
                f.to_dict() for f in validation.findings]

        return ShowAnalysis.redact(analysis)

    def _provenance(self, script: BroadcastScript) -> dict[str, Any]:
        """§18.4 field 9: model, prompt version, evidence packet, script version.

        Assembled from the turns' generation metadata. No reasoning fields.
        """
        provenance: dict[str, Any] = {
            "script_version": script.version,
            "evidence_packet_id": script.evidence_packet_id,
            "generation_request_id": script.generation_request_id,
            "world_snapshot_id": script.world_snapshot_id,
        }
        for turn in script.turns:
            metadata = turn.generation_metadata or {}
            for key in ("provider_id", "model_id", "prompt_version",
                        "schema_version"):
                if key in metadata and key not in provenance:
                    provenance[key] = metadata[key]
        return provenance
