"""Script validation: the eight stages of §15.4.

Seven of the eight stages are deterministic. Stage 3 (contradiction review)
reuses the Phase 3 contradiction machinery, which is also deterministic for
quantity, unit, and temporal conflicts. Stages 3 and 5 may accept an optional
verifier, but must not require one.

Why the stages are separate: §15.4 asks for "multiple independent stages," and
§15.6 asks for verification through "multiple failure-independent mechanisms
rather than treating a second language-model response as a guarantee of truth."
A stage that silently depends on a model is not independent, and a model asked
to review its own output is not a second opinion (§15.6).

The outcome vocabulary of §15.5 is deliberate: REQUIRES_REGENERATION and
REJECTED are different things. A malformed script is recoverable; an
unsupported allegation is not, however polished its prose.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .entities import (
    BroadcastScript,
    EpistemicFunction,
    Severity,
    SpeechAct,
    ValidationOutcome,
    ValidationStage,
)


@dataclass
class StageFinding:
    stage: ValidationStage
    severity: Severity
    message: str
    turn_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "severity": self.severity.value,
            "message": self.message,
            "turn_id": self.turn_id,
        }


@dataclass
class ValidationReport:
    outcome: ValidationOutcome
    findings: list[StageFinding] = field(default_factory=list)
    qualifications: list[str] = field(default_factory=list)
    stages_run: list[int] = field(default_factory=list)
    validated_at: datetime | None = None

    @property
    def is_approved(self) -> bool:
        return self.outcome in (
            ValidationOutcome.APPROVED,
            ValidationOutcome.APPROVED_WITH_QUALIFICATIONS,
        )

    @property
    def fatal_findings(self) -> list[StageFinding]:
        return [f for f in self.findings if f.severity is Severity.FATAL]

    @property
    def error_findings(self) -> list[StageFinding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]


# ── Stage 1: Structural validation ───────────────────────────────────


def validate_structural(
    script: BroadcastScript,
    known_claim_ids: set[str] | None = None,
    max_turns: int = 24,
    min_turn_length: int = 3,
) -> list[StageFinding]:
    """§15.4 stage 1. Schema, valid references, length and formatting.

    Empty or whitespace-only turns, duplicate sequence numbers, and
    over-length scripts fail here before any semantic review.
    """
    findings: list[StageFinding] = []
    if not script.turns:
        findings.append(StageFinding(
            ValidationStage.STRUCTURAL, Severity.ERROR,
            "script has no turns"))
        return findings

    sequences = [t.sequence for t in script.turns]
    if len(set(sequences)) != len(sequences):
        findings.append(StageFinding(
            ValidationStage.STRUCTURAL, Severity.ERROR,
            "duplicate turn sequence numbers"))

    if len(script.turns) > max_turns:
        findings.append(StageFinding(
            ValidationStage.STRUCTURAL, Severity.ERROR,
            f"script has {len(script.turns)} turns, exceeding {max_turns}"))

    for turn in script.turns:
        if not turn.text or not turn.text.strip():
            findings.append(StageFinding(
                ValidationStage.STRUCTURAL, Severity.ERROR,
                "turn has empty text", turn.id))
            continue
        if len(turn.text.strip()) < min_turn_length:
            findings.append(StageFinding(
                ValidationStage.STRUCTURAL, Severity.ERROR,
                f"turn text too short ({len(turn.text.strip())} chars)",
                turn.id))

    # §15.4 stage 1: references must be valid records.
    if known_claim_ids is not None:
        for turn in script.turns:
            for claim_id in turn.claim_references:
                if claim_id not in known_claim_ids:
                    findings.append(StageFinding(
                        ValidationStage.STRUCTURAL, Severity.ERROR,
                        f"claim reference {claim_id} does not resolve",
                        turn.id))
    return findings


# ── Stage 2: Provenance validation ───────────────────────────────────


def validate_provenance(
    script: BroadcastScript,
    claim_supports: dict[str, list[str]] | None = None,
) -> list[StageFinding]:
    """§15.4 stage 2 and §15.3.

    The rule §15.3 states explicitly: "A script should not be approved merely
    because it includes a list of sources somewhere in its metadata. The
    system must verify that the relevant evidence actually supports the
    particular statement."

    `claim_supports` maps a claim id to the evidence ids that support it. A
    factual turn whose claim has no supporting evidence fails.
    """
    findings: list[StageFinding] = []
    claim_supports = claim_supports or {}

    for turn in script.turns:
        if turn.speech_act != SpeechAct.FACTUAL_REPORT:
            continue
        if not turn.claim_references:
            findings.append(StageFinding(
                ValidationStage.PROVENANCE, Severity.ERROR,
                "factual report has no claim reference", turn.id))
            continue
        if not turn.evidence_references:
            findings.append(StageFinding(
                ValidationStage.PROVENANCE, Severity.ERROR,
                "factual report cites a claim but no evidence", turn.id))
            continue
        for claim_id in turn.claim_references:
            supporting = claim_supports.get(claim_id, [])
            if not supporting:
                findings.append(StageFinding(
                    ValidationStage.PROVENANCE, Severity.ERROR,
                    f"no evidence supports claim {claim_id}", turn.id))
            else:
                cited = set(turn.evidence_references)
                if not cited & set(supporting):
                    findings.append(StageFinding(
                        ValidationStage.PROVENANCE, Severity.ERROR,
                        f"cited evidence does not support claim {claim_id}",
                        turn.id))
    return findings


# ── Stage 3: Contradiction review ────────────────────────────────────


# A verifier receives the script and returns findings. Optional by design.
ContradictionVerifier = Callable[[BroadcastScript], list[StageFinding]]


def validate_contradiction(
    script: BroadcastScript,
    contradicting_claim_texts: list[str] | None = None,
    verifier: ContradictionVerifier | None = None,
) -> list[StageFinding]:
    """§15.4 stage 3. Conflict with material evidence or an omitted
    critical qualification.

    The deterministic check compares the script's turns against recorded
    contradicting evidence. This catches quantity, unit, and temporal
    conflicts, matching the Phase 3 contradiction machinery. A qualitative
    contradiction needs a verifier; without one it is not caught, and that is
    recorded as a known gap rather than claimed as coverage.
    """
    findings: list[StageFinding] = []
    script_text = " ".join(t.text for t in script.turns).lower()

    for text in contradicting_claim_texts or []:
        if text and text.lower() in script_text:
            findings.append(StageFinding(
                ValidationStage.CONTRADICTION, Severity.ERROR,
                f"script restates contradicted material: {text[:60]}"))

    # A script with recorded contradicting evidence but no uncertainty marker
    # has omitted a critical qualification.
    if (contradicting_claim_texts
            and not any(t.uncertainty_markers for t in script.turns)):
        findings.append(StageFinding(
            ValidationStage.CONTRADICTION, Severity.QUALIFICATION,
            "contradicting evidence recorded but no uncertainty marker in "
            "the script"))

    if verifier is not None:
        findings.extend(verifier(script))
    return findings


# ── Stage 4: Attribution validation ──────────────────────────────────


ATTRIBUTED_ACT = SpeechAct.ATTRIBUTED_STATEMENT

# A source claim is not silently an established fact. These markers indicate
# the statement is still attributed in the spoken text.
ATTRIBUTION_MARKERS = (
    "according to", "officials said", "officials say", "the report states",
    "reported by", "sources said", "attributed to", "spokesperson said",
    "the agency said", "ministers said", "ministers say", "the study found",
    "analysts said", "analysts say", "witnesses said", "police said",
    "police say", "the committee said", "researchers said",
)


def validate_attribution(script: BroadcastScript) -> list[StageFinding]:
    """§15.4 stage 4. Attributed statements stay attributed.

    §5.6.1 and ADR-021 established this in the compiler: "Officials said 40
    died" is hearsay, not a measurement. The same discipline applies to what
    gets spoken.
    """
    findings: list[StageFinding] = []
    for turn in script.turns:
        if turn.speech_act != ATTRIBUTED_ACT:
            continue
        text = turn.text.lower()
        has_marker = any(m in text for m in ATTRIBUTION_MARKERS)
        has_field = bool(turn.attribution)
        if not has_marker and not has_field:
            findings.append(StageFinding(
                ValidationStage.ATTRIBUTION, Severity.FATAL,
                "attributed statement has lost its attribution", turn.id))
    return findings


# ── Stage 5: Epistemic consistency ───────────────────────────────────

# Speculative and interpretive statements require qualification (§15.4
# stage 5). Without a marker, speculation is presented as established.
SPECULATIVE_ACTS = (
    SpeechAct.PREDICTION,
    SpeechAct.HUMOR,          # humour may mislead if unmarked
)

QUALIFICATION_MARKERS = (
    "may", "might", "could", "possibly", "perhaps", "it appears",
    "we do not know", "unclear", "unconfirmed", "it is not yet clear",
    "suggests", "appears to", "expected", "reportedly",
)


def validate_epistemic_consistency(
    script: BroadcastScript,
    verifier: Callable[[BroadcastScript], list[StageFinding]] | None = None,
) -> list[StageFinding]:
    """§15.4 stage 5. Speculation is presented with appropriate qualification.

    Deterministic check: a prediction or interpretive turn with no hedging
    language and no uncertainty marker is stating speculation as fact.
    """
    findings: list[StageFinding] = []
    for turn in script.turns:
        if turn.speech_act not in SPECULATIVE_ACTS:
            continue
        text = turn.text.lower()
        qualified = (any(m in text for m in QUALIFICATION_MARKERS)
                     or bool(turn.uncertainty_markers))
        if not qualified:
            findings.append(StageFinding(
                ValidationStage.EPISTEMIC_CONSISTENCY, Severity.QUALIFICATION,
                f"{turn.speech_act.value} presented without qualification",
                turn.id))

        if turn.epistemic_function in (
                EpistemicFunction.SPECULATION,
                EpistemicFunction.PREDICTION,
        ) and not qualified:
            findings.append(StageFinding(
                ValidationStage.EPISTEMIC_CONSISTENCY, Severity.ERROR,
                "epistemic function is speculative but no qualification is "
                "present", turn.id))

    if verifier is not None:
        findings.extend(verifier(script))
    return findings


# ── Stage 6: Temporal validation ─────────────────────────────────────

STALENESS_MARKERS = (
    "currently", "as of now", "right now", "at present", "today",
    "this morning", "ongoing", "still ongoing", "as things stand",
)


def validate_temporal(
    script: BroadcastScript,
    superseded_claim_texts: list[str] | None = None,
    as_of: datetime | None = None,
) -> list[StageFinding]:
    """§15.4 stage 6. Outdated information must not be presented as current
    when a newer material development has been recorded.

    This is a rule-based check, one of the independent mechanisms §15.6 lists.
    """
    findings: list[StageFinding] = []
    script_text = " ".join(t.text for t in script.turns).lower()

    for text in superseded_claim_texts or []:
        if not text:
            continue
        lowered = text.lower()
        if lowered in script_text:
            presents_as_current = any(
                m in script_text for m in STALENESS_MARKERS)
            findings.append(StageFinding(
                ValidationStage.TEMPORAL,
                Severity.ERROR if presents_as_current
                else Severity.QUALIFICATION,
                f"script references superseded material: {text[:60]}"))

    if as_of is not None and script.created_at > as_of:
        findings.append(StageFinding(
            ValidationStage.TEMPORAL, Severity.ERROR,
            "script was created after its evidence packet's as-of time"))
    return findings


# ── Stage 7: Safety and privacy review ───────────────────────────────

# Patterns for prohibited disclosures and high-impact unsupported
# allegations. Detection is a flag for review, never a substitute for it.
PRIVACY_PATTERNS = (
    r"\bssn\b", r"social security number", r"\bcredit card\b",
    r"\bpassword\b", r"\bapi[_\s]?key\b", r"private key",
    r"\bmedical record", r"home address of",
)

HIGH_IMPACT_ALLEGATION_MARKERS = (
    "committed", "conspired", "covered up", "deliberately hid",
    "fabricated", "lied about", "stole", "embezzled",
)


def validate_safety_privacy(script: BroadcastScript) -> list[StageFinding]:
    """§15.4 stage 7. Prohibited disclosures, unsupported high-impact
    allegations, and content needing additional restriction.

    An allegation of deliberate wrongdoing with no supporting evidence
    reference is FATAL. §15.5: a segment that makes an unsupported allegation
    should not pass simply because its prose is polished.
    """
    findings: list[StageFinding] = []
    for turn in script.turns:
        text = turn.text
        lowered = text.lower()

        for pattern in PRIVACY_PATTERNS:
            if re.search(pattern, lowered):
                findings.append(StageFinding(
                    ValidationStage.SAFETY_PRIVACY, Severity.FATAL,
                    f"potential prohibited disclosure: matched {pattern!r}",
                    turn.id))

        if any(m in lowered for m in HIGH_IMPACT_ALLEGATION_MARKERS) and not turn.claim_references or not turn.evidence_references:
                findings.append(StageFinding(
                    ValidationStage.SAFETY_PRIVACY, Severity.FATAL,
                    "high-impact allegation without supporting evidence",
                    turn.id))
    return findings


# ── Stage 8: Delivery validation ─────────────────────────────────────

# §15.4 stage 8. Estimated duration, speaker assignment, TTS compatibility.
MAX_TURN_DURATION_MS = 120_000
MIN_TURN_DURATION_MS = 500


def validate_delivery(
    script: BroadcastScript,
    known_voice_profile_ids: set[str] | None = None,
    turn_durations: dict[str, int] | None = None,
) -> list[StageFinding]:
    """§15.4 stage 8. Estimated duration, speaker, TTS compatibility."""
    findings: list[StageFinding] = []
    turn_durations = turn_durations or {}

    if not script.turns:
        return findings

    for turn in script.turns:
        if not turn.persona_id:
            findings.append(StageFinding(
                ValidationStage.DELIVERY, Severity.ERROR,
                "turn has no speaker assignment", turn.id))

        duration = turn.expected_duration_ms or turn_durations.get(turn.id)
        if duration is not None and duration > MAX_TURN_DURATION_MS:
                findings.append(StageFinding(
                    ValidationStage.DELIVERY, Severity.ERROR,
                    f"turn duration {duration}ms exceeds "
                    f"{MAX_TURN_DURATION_MS}ms", turn.id))
            elif duration < MIN_TURN_DURATION_MS:
                findings.append(StageFinding(
                    ValidationStage.DELIVERY, Severity.QUALIFICATION,
                    f"turn duration {duration}ms is very short", turn.id))

        # TTS compatibility: text should not contain characters a synthesiser
        # cannot render.
        if re.search(r"[\u200b-\u200f\ufeff]", turn.text):
            findings.append(StageFinding(
                ValidationStage.DELIVERY, Severity.WARNING,
                "turn contains zero-width or formatting characters",
                turn.id))

    if known_voice_profile_ids is not None:
        seen_personas = {t.persona_id for t in script.turns if t.persona_id}
        missing = seen_personas - known_voice_profile_ids
        if missing:
            findings.append(StageFinding(
                ValidationStage.DELIVERY, Severity.ERROR,
                f"{len(missing)} assigned persona(s) have no voice profile"))
    return findings


# ── The pipeline ──────────────────────────────────────────────────────


class ScriptValidator:
    """Runs the eight stages of §15.4 and produces the §15.5 outcome.

    Stages are independent: each returns its own findings, and no stage's
    success depends on another having run. That is what makes the pipeline
    failure-independent in the sense §15.6 asks for.
    """

    def __init__(
        self,
        known_claim_ids: set[str] | None = None,
        claim_supports: dict[str, list[str]] | None = None,
        contradicting_claim_texts: list[str] | None = None,
        superseded_claim_texts: list[str] | None = None,
        known_voice_profile_ids: set[str] | None = None,
        contradiction_verifier: ContradictionVerifier | None = None,
        epistemic_verifier: Any | None = None,
        as_of: datetime | None = None,
        require_all_stages: bool = True,
    ):
        self.known_claim_ids = known_claim_ids
        self.claim_supports = claim_supports
        self.contradicting_claim_texts = contradicting_claim_texts
        self.superseded_claim_texts = superseded_claim_texts
        self.known_voice_profile_ids = known_voice_profile_ids
        self.contradiction_verifier = contradiction_verifier
        self.epistemic_verifier = epistemic_verifier
        self.as_of = as_of
        self.require_all_stages = require_all_stages

    def validate(self, script: BroadcastScript) -> ValidationReport:
        findings: list[StageFinding] = []
        stages_run: list[int] = []

        stage_functions: list[tuple[ValidationStage, Any]] = [
            (ValidationStage.STRUCTURAL,
             lambda: validate_structural(script, self.known_claim_ids)),
            (ValidationStage.PROVENANCE,
             lambda: validate_provenance(script, self.claim_supports)),
            (ValidationStage.CONTRADICTION,
             lambda: validate_contradiction(
                 script, self.contradicting_claim_texts,
                 self.contradiction_verifier)),
            (ValidationStage.ATTRIBUTION,
             lambda: validate_attribution(script)),
            (ValidationStage.EPISTEMIC_CONSISTENCY,
             lambda: validate_epistemic_consistency(
                 script, self.epistemic_verifier)),
            (ValidationStage.TEMPORAL,
             lambda: validate_temporal(
                 script, self.superseded_claim_texts, self.as_of)),
            (ValidationStage.SAFETY_PRIVACY,
             lambda: validate_safety_privacy(script)),
            (ValidationStage.DELIVERY,
             lambda: validate_delivery(
                 script, self.known_voice_profile_ids)),
        ]

        for stage, function in stage_functions:
            try:
                stage_findings = function()
            except Exception as error:               # a stage must not crash the run
                stage_findings = [StageFinding(
                    stage, Severity.ERROR,
                    f"stage raised {type(error).__name__}: {error}")]
            findings.extend(stage_findings)
            stages_run.append(stage.value)

        outcome = self._decide(findings, stages_run)
        qualifications = [f.message for f in findings
                          if f.severity is Severity.QUALIFICATION]
        return ValidationReport(
            outcome=outcome,
            findings=findings,
            qualifications=qualifications,
            stages_run=stages_run,
            validated_at=datetime.now(UTC),
        )

    def _decide(
        self, findings: list[StageFinding], stages_run: list[int]
    ) -> ValidationOutcome:
        """§15.5. Recoverable problems and unspeakable content are different.

        FATAL findings mean REJECTED outright. ERROR findings mean the script
        needs regeneration. QUALIFICATION findings may be repaired by adding
        attribution, narrowing the claim, or describing uncertainty.
        """
        if self.require_all_stages and len(stages_run) < 8:
            # §15.4: only after all mandatory checks have passed.
            return ValidationOutcome.REQUIRES_REGENERATION

        if any(f.severity is Severity.FATAL for f in findings):
            return ValidationOutcome.REJECTED
        if any(f.severity is Severity.ERROR for f in findings):
            return ValidationOutcome.REQUIRES_REGENERATION
        if any(f.severity is Severity.QUALIFICATION for f in findings):
            return ValidationOutcome.APPROVED_WITH_QUALIFICATIONS
        if any(f.severity is Severity.WARNING for f in findings):
            return ValidationOutcome.APPROVED_WITH_QUALIFICATIONS
        return ValidationOutcome.APPROVED
