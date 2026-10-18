"""Persona domain model.

Spec §12.

The distinction this module exists to enforce is §12.3: a persona's
personality may influence vocabulary, humor, pace, depth, analogies, the
questions it asks, and its willingness to challenge another persona — but it
**must not independently determine whether a factual proposition is true.**

The shared knowledge model provides a common evidence baseline. Personas
disagree about significance, mechanism, and implication while agreeing about
what the evidence contains. That separation is what makes analytical debate
meaningful rather than theatrical.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any

from ..models import _new_id, _utcnow

# ── §12.5 Disagreement types ─────────────────────────────────────────


class DisagreementType(Enum):
    """§12.5. Each type implies a different kind of resolution."""
    FACTUAL_UNCERTAINTY = "factual_uncertainty"
    COMPETING_CAUSAL_EXPLANATIONS = "competing_causal_explanations"
    DIFFERENT_VALUE_JUDGMENTS = "different_value_judgments"
    DIFFERENT_FORECASTS = "different_forecasts"
    DIFFERENT_INSTITUTIONAL_INTERPRETATIONS = "different_institutional_interpretations"
    DIFFERENT_EVIDENCE_ASSESSMENTS = "different_evidence_assessments"
    DIFFERENT_POLICY_RECOMMENDATIONS = "different_policy_recommendations"


class ResolutionStatus(Enum):
    """§12.5 resolution_status."""
    UNRESOLVED = "unresolved"
    RESOLVED_BY_EVIDENCE = "resolved_by_evidence"
    RESOLVED_BY_CONVENTION = "resolved_by_convention"
    NOT_APPLICABLE = "not_applicable"


# ── §12.6 Memory types ───────────────────────────────────────────────


class MemoryType(Enum):
    """§12.6. The five kinds, kept distinct."""
    EPISODIC = "episodic"                    # what happened in a broadcast
    SEMANTIC = "semantic"                    # generalized stable conclusions
    PREFERENCE = "preference"                # established style and editorial prefs
    OPEN_QUESTION = "open_question"          # unresolved questions and hypotheses
    CORRECTION = "correction"                # prior conclusions revised or withdrawn


class MemoryValidity(Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


# ── §12.2 Analytical preferences ─────────────────────────────────────


@dataclass
class AnalyticalPreferences:
    """§12.2. Separated so each can be reasoned about independently.

    epistemic_preferences govern how the persona treats evidence — never what
    is true (§12.3).
    """
    analytical_style: str = ""
    epistemic: dict[str, Any] = field(default_factory=dict)
    rhetorical: dict[str, Any] = field(default_factory=dict)
    interaction: dict[str, Any] = field(default_factory=dict)
    speaking_style: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "analytical_style": self.analytical_style,
            "epistemic": dict(self.epistemic),
            "rhetorical": dict(self.rhetorical),
            "interaction": dict(self.interaction),
            "speaking_style": dict(self.speaking_style),
        }


# ── §12.2 Persona configuration ──────────────────────────────────────


@dataclass
class PersonaConfig:
    """§12.2. Identity and editorial instructions stored separately from
    transient generation prompts.

    An update creates a versioned change, never a silent rewrite of the
    instructions used by a previously generated segment.
    """
    id: str = field(default_factory=_new_id)
    name: str = ""
    role: str = ""
    description: str = ""
    voice_profile_id: str | None = None
    preferences: AnalyticalPreferences = field(
        default_factory=AnalyticalPreferences)
    memory_policy: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    configuration_version: int = 1
    created_at: datetime = field(default_factory=_utcnow)
    superseded_by_version: int | None = None

    def revised(self, **changes) -> "PersonaConfig":
        """§12.2: a new version, not an in-place edit.

        The previous version is retained with a pointer forward, so a segment
        generated under version 1 can still be traced to the instructions that
        produced it.
        """
        updated = replace(self, **changes)
        updated.configuration_version = self.configuration_version + 1
        updated.superseded_by_version = None
        return updated

    def retire(self) -> "PersonaConfig":
        retired = replace(self)
        retired.enabled = False
        retired.superseded_by_version = self.configuration_version + 1
        return retired


# ── §12.4 Independent analysis ───────────────────────────────────────


@dataclass
class PersonaAnalysis:
    """§12.4. A first-pass analysis with its assumptions recorded.

    Step 4 requires recording "the assumptions and evidence used by each
    analysis." Without that record, a later disagreement cannot be attributed
    to a difference in assumption rather than a difference in evidence.
    """
    id: str = field(default_factory=_new_id)
    persona_id: str = ""
    persona_version: int = 1
    question: str = ""
    position: str = ""
    assumptions: list[str] = field(default_factory=list)
    evidence_used: list[str] = field(default_factory=list)
    shared_facts_accepted: list[str] = field(default_factory=list)
    confidence: float = 0.0
    generated_at: datetime = field(default_factory=_utcnow)

    def assumptions_signature(self) -> str:
        """A stable key for comparing assumptions across personas."""
        return "|".join(sorted(a.lower() for a in self.assumptions))


# ── §12.5 Disagreement ───────────────────────────────────────────────


@dataclass
class Disagreement:
    """§12.5. A useful disagreement identifies the precise proposition.

    The `shared_facts` field is what separates a real disagreement from a
    misunderstanding: both parties accept these, and dispute only the
    proposition or the assumptions behind it.
    """
    id: str = field(default_factory=_new_id)
    subject: str = ""
    proposition: str = ""
    persona_a_id: str = ""
    position_a: str = ""
    persona_b_id: str = ""
    position_b: str = ""
    shared_facts: list[str] = field(default_factory=list)
    disputed_assumptions: list[str] = field(default_factory=list)
    evidence_references: list[str] = field(default_factory=list)
    disagreement_type: DisagreementType = DisagreementType.FACTUAL_UNCERTAINTY
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    detected_at: datetime = field(default_factory=_utcnow)

    @property
    def is_substantive(self) -> bool:
        """A disagreement with no disputed proposition is not a disagreement."""
        return bool(self.proposition and self.proposition.strip())

    def involves(self, persona_id: str) -> bool:
        return persona_id in (self.persona_a_id, self.persona_b_id)


# ── §12.6 Persona memory ─────────────────────────────────────────────


@dataclass
class PersonaMemoryEntry:
    """§12.6. Typed, provenance-carrying, with a validity status.

    A persona should be able to remember it previously argued for a position
    while also recognizing that subsequent evidence weakened that position.
    That is the CORRECTION memory type pointing back at the SEMANTIC entry it
    revised.
    """
    id: str = field(default_factory=_new_id)
    persona_id: str = ""
    memory_type: MemoryType = MemoryType.EPISODIC
    content: str = ""
    provenance: str = ""
    source_segment_id: str | None = None
    evidence_references: list[str] = field(default_factory=list)
    validity: MemoryValidity = MemoryValidity.ACTIVE
    confidence: float = 0.5
    created_at: datetime = field(default_factory=_utcnow)
    revises_entry_id: str | None = None

    def to_context_line(self) -> str:
        """The compact form that enters context. Bounded by the retrieval cap."""
        return f"[{self.memory_type.value}] {self.content}"


# ── §12.7 Consistency ────────────────────────────────────────────────


@dataclass
class ConsistencyDimension:
    name: str
    score: float          # 0.0 (problem) to 1.0 (consistent)
    detail: str = ""

    @property
    def is_concerning(self) -> bool:
        return self.score < 0.5


@dataclass
class ConsistencyReport:
    """§12.7. The six measured dimensions."""
    persona_id: str = ""
    dimensions: list[ConsistencyDimension] = field(default_factory=list)
    evaluated_at: datetime = field(default_factory=_utcnow)

    @property
    def overall(self) -> float:
        if not self.dimensions:
            return 0.0
        return sum(d.score for d in self.dimensions) / len(self.dimensions)

    @property
    def concerning(self) -> list[ConsistencyDimension]:
        return [d for d in self.dimensions if d.is_concerning]

    def dimension(self, name: str) -> ConsistencyDimension | None:
        for d in self.dimensions:
            if d.name == name:
                return d
        return None


# ── §12.4 Dialogue exchange ──────────────────────────────────────────


@dataclass
class DialogueTurn:
    persona_id: str
    text: str
    addresses_disagreement_id: str | None = None
    speech_act: str = "interpretation"


@dataclass
class StructuredExchange:
    """§12.4 step 6. Built from recorded disagreements, not improvised."""
    id: str = field(default_factory=_new_id)
    question: str = ""
    turns: list[DialogueTurn] = field(default_factory=list)
    disagreements_addressed: list[str] = field(default_factory=list)
    shared_facts: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utcnow)

    @property
    def personas(self) -> list[str]:
        seen: list[str] = []
        for turn in self.turns:
            if turn.persona_id not in seen:
                seen.append(turn.persona_id)
        return seen
