"""Broadcast domain model.

Spec §12 (persona), §13 (director and segment lifecycle), §14 (generation
requests), §15 (scripts and validation), §16 (TTS and audio).

Distinctions this module exists to enforce:

- Generated text, validated text, submitted text, synthesized audio, buffered
  audio, and consumed audio are six different things (§13.4). Collapsing them
  is how a system ends up claiming it broadcast something it never spoke.
- A script is a persistent artifact, not an ephemeral string (§15.1).
- A voice profile is not a persona (§16.3).
- Exactly-once delivery is not assumed (§13.6).
"""

from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from ..models import _new_id, _utcnow


# ── §13.3 Segment lifecycle ──────────────────────────────────────────


class SegmentStatus(Enum):
    """§13.3. A proposed segment is not an active segment (§11.7).

    The order is load-bearing. A segment cannot be synthesized before it is
    validated, and cannot be queued before audio exists.
    """
    PROPOSED = "proposed"
    PLANNED = "planned"
    GENERATING = "generating"
    GENERATED = "generated"
    VALIDATING = "validating"
    APPROVED = "approved"
    SYNTHESIZING = "synthesizing"
    READY = "ready"
    QUEUED = "queued"
    PLAYING = "playing"
    COMPLETED = "completed"

    # Failure and cancellation states, required at each appropriate stage.
    REJECTED = "rejected"              # content must not be broadcast
    FAILED = "failed"                  # recoverable technical failure
    CANCELLED = "cancelled"            # withdrawn before delivery
    SUPERSEDED = "superseded"          # replaced by a newer generation
    INTERRUPTED = "interrupted"        # abandoned mid-playback (§13.5)


# Legal transitions (§13.3). Everything not listed is illegal, and the
# director refuses it rather than logging a warning.
SEGMENT_TRANSITIONS: Dict[SegmentStatus, set] = {
    SegmentStatus.PROPOSED: {SegmentStatus.PLANNED, SegmentStatus.CANCELLED},
    SegmentStatus.PLANNED: {
        SegmentStatus.GENERATING, SegmentStatus.CANCELLED,
        SegmentStatus.SUPERSEDED,
    },
    SegmentStatus.GENERATING: {
        SegmentStatus.GENERATED, SegmentStatus.FAILED,
        SegmentStatus.CANCELLED,
    },
    SegmentStatus.GENERATED: {
        SegmentStatus.VALIDATING, SegmentStatus.CANCELLED,
    },
    SegmentStatus.VALIDATING: {
        # A script that fails validation may return to generation.
        SegmentStatus.APPROVED, SegmentStatus.GENERATING,
        SegmentStatus.REJECTED, SegmentStatus.FAILED,
    },
    SegmentStatus.APPROVED: {
        SegmentStatus.SYNTHESIZING, SegmentStatus.CANCELLED,
        SegmentStatus.SUPERSEDED,
    },
    SegmentStatus.SYNTHESIZING: {
        SegmentStatus.READY, SegmentStatus.FAILED, SegmentStatus.CANCELLED,
    },
    # A TTS failure does NOT invalidate the evidence or require a new script.
    # The segment returns to APPROVED and the media subsystem retries (§13.3).
    SegmentStatus.FAILED: {
        SegmentStatus.SYNTHESIZING, SegmentStatus.GENERATING,
        SegmentStatus.APPROVED, SegmentStatus.CANCELLED,
    },
    SegmentStatus.READY: {SegmentStatus.QUEUED, SegmentStatus.CANCELLED},
    SegmentStatus.QUEUED: {
        SegmentStatus.PLAYING, SegmentStatus.CANCELLED,
        SegmentStatus.SUPERSEDED, SegmentStatus.INTERRUPTED,
    },
    SegmentStatus.PLAYING: {
        SegmentStatus.COMPLETED, SegmentStatus.INTERRUPTED,
        SegmentStatus.FAILED,
    },
    SegmentStatus.INTERRUPTED: {
        # Interrupted speech is recorded; it does not silently vanish and it
        # must not be able to resume into the queue as though complete.
        SegmentStatus.PLAYING, SegmentStatus.CANCELLED,
        SegmentStatus.COMPLETED,
    },
    SegmentStatus.COMPLETED: set(),
    SegmentStatus.REJECTED: set(),
    SegmentStatus.CANCELLED: set(),
    SegmentStatus.SUPERSEDED: set(),
}


class SegmentTransitionError(Exception):
    """An attempted illegal segment transition."""


# ── §15.1 Speech acts and §15.2 epistemic functions ─────────────────


class SpeechAct(Enum):
    """§15.1. Which validation procedures apply depends on this."""
    FACTUAL_REPORT = "factual_report"
    ATTRIBUTED_STATEMENT = "attributed_statement"
    INTERPRETATION = "interpretation"
    QUESTION = "question"
    PREDICTION = "prediction"
    HUMOR = "humor"
    TRANSITION = "transition"


class EpistemicFunction(Enum):
    """§15.2. Internal metadata even when not spoken aloud.

    A single sentence may contain an attributed statement followed by an
    inference, so classification can apply at clause level.
    """
    FACTUAL_REPORT = "factual_report"
    ATTRIBUTED_STATEMENT = "attributed_statement"
    EVIDENCE_BASED_INTERPRETATION = "evidence_based_interpretation"
    INFERENCE = "inference"
    SPECULATION = "speculation"
    PREDICTION = "prediction"
    OPINION = "opinion"
    QUESTION = "question"
    HYPOTHETICAL = "hypothetical"
    HUMOR = "humor"
    TRANSITION = "transition"


# ── §15.4 Validation stages and §15.5 outcomes ──────────────────────


class ValidationStage(Enum):
    STRUCTURAL = 1
    PROVENANCE = 2
    CONTRADICTION = 3
    ATTRIBUTION = 4
    EPISTEMIC_CONSISTENCY = 5
    TEMPORAL = 6
    SAFETY_PRIVACY = 7
    DELIVERY = 8


class ValidationOutcome(Enum):
    """§15.5. Distinguishes recoverable problems from unspeakable content.

    A polished unsupported allegation must not pass as APPROVED_WITH_
    QUALIFICATIONS.
    """
    APPROVED = "approved"
    APPROVED_WITH_QUALIFICATIONS = "approved_with_qualifications"
    REQUIRES_REGENERATION = "requires_regeneration"
    REQUIRES_REVIEW = "requires_review"
    REJECTED = "rejected"


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    QUALIFICATION = "qualification"      # repairable by narrowing or attributing
    ERROR = "error"                      # blocks approval
    FATAL = "fatal"                      # must never be broadcast


# ── §11.6 Interruption actions ───────────────────────────────────────


class InterruptionAction(Enum):
    CONTINUE = "continue"
    QUEUE_NEXT = "queue_next"
    INTERRUPT = "interrupt"


# ── §13.2 Playback state ─────────────────────────────────────────────


class PlaybackState(Enum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    INTERRUPTED = "interrupted"
    STOPPED = "stopped"


# ── §13.4 The six distinct content states ───────────────────────────


class ContentState(Enum):
    """§13.4. Generated, validated, submitted, synthesized, buffered, and
    consumed are separate events.

    A server cannot infer a listener heard a segment because it wrote audio
    bytes to a socket.
    """
    GENERATED = "generated"
    VALIDATED = "validated"
    SUBMITTED_TO_TTS = "submitted_to_tts"
    SYNTHESIZED = "synthesized"
    BUFFERED = "buffered"
    DELIVERED = "delivered"
    CONSUMED = "consumed"


# ── §12 Persona ──────────────────────────────────────────────────────


@dataclass
class Persona:
    """§12. A single persona in Phase 5.

    editorial_constraints are what the persona may not do; persona_
    configuration_version is recorded on every request so a later change is
    auditable.
    """
    id: str = dc_field(default_factory=_new_id)
    name: str = ""
    description: str = ""
    configuration_version: str = "1.0.0"
    voice_profile_id: Optional[str] = None
    editorial_constraints: List[str] = dc_field(default_factory=list)
    uncertainty_policy: str = "explicit"
    created_at: datetime = dc_field(default_factory=_utcnow)


# ── §16.3 Voice profile ──────────────────────────────────────────────


@dataclass
class VoiceProfile:
    """§16.3. Separate from a persona.

    Not all providers support all parameters. Unsupported settings are
    rejected or explicitly ignored per a documented policy — never silently
    misapplied.
    """
    id: str = dc_field(default_factory=_new_id)
    provider_id: str = ""
    voice_id: str = ""
    language: str = "en"
    default_rate: float = 1.0
    pitch_parameters: Dict[str, Any] = dc_field(default_factory=dict)
    output_format: str = "pcm_s16le"
    normalization_policy: str = "none"
    configuration_version: str = "1.0.0"


# ── §14.3 Generation request ─────────────────────────────────────────


@dataclass
class EvidencePacket:
    """§14.3. Built by application code, not discovered by the model.

    §14.3: "A model should not be asked to independently discover the
    authoritative state of the world from a large collection of loosely
    related documents when the application can supply a targeted, versioned
    set of relevant evidence."
    """
    id: str = dc_field(default_factory=_new_id)
    world_snapshot_id: str = ""
    segment_objective: str = ""
    supporting_claims: List[str] = dc_field(default_factory=list)
    contradicting_claims: List[str] = dc_field(default_factory=list)
    evidence_ids: List[str] = dc_field(default_factory=list)
    historical_context_ids: List[str] = dc_field(default_factory=list)
    previously_broadcast_ids: List[str] = dc_field(default_factory=list)
    unresolved_questions: List[str] = dc_field(default_factory=list)
    required_disclaimers: List[str] = dc_field(default_factory=list)
    persona_memory_ids: List[str] = dc_field(default_factory=list)
    # §14.4: the packet preserves the distinction between source material,
    # system summaries, and persona interpretation.
    content_provenance: Dict[str, str] = dc_field(default_factory=dict)
    created_at: datetime = dc_field(default_factory=_utcnow)

    @property
    def claim_ids(self) -> List[str]:
        return self.supporting_claims + self.contradicting_claims


@dataclass
class GenerationRequest:
    """§14.3. Every generation carries explicit context and constraints.

    idempotency_key exists so a retry does not create a duplicate active
    segment (§14.6).
    """
    id: str = dc_field(default_factory=_new_id)
    idempotency_key: str = ""
    task_type: str = ""
    segment_id: str = ""
    input_record_ids: List[str] = dc_field(default_factory=list)
    evidence_packet_id: str = ""
    persona_id: str = ""
    persona_configuration_version: str = ""
    prompt_version: str = ""
    output_schema_version: str = ""
    model_selection_policy: str = "local_default"
    maximum_output_tokens: int = 2048
    created_at: datetime = dc_field(default_factory=_utcnow)
    deadline: Optional[datetime] = None
    max_attempts: int = 2
    metadata: Dict[str, Any] = dc_field(default_factory=dict)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        if self.deadline is None:
            return False
        return (now or _utcnow()) >= self.deadline


@dataclass
class GenerationAttempt:
    """§14.6. The record the spec requires on every attempt.

    Retries must not be invisible; a repeated semantic failure must not become
    an infinite regeneration loop.
    """
    id: str = dc_field(default_factory=_new_id)
    request_id: str = ""
    attempt_number: int = 1
    provider_id: str = ""
    model_id: str = ""
    prompt_version: str = ""
    schema_version: str = ""
    started_at: datetime = dc_field(default_factory=_utcnow)
    completed_at: Optional[datetime] = None
    latency_ms: Optional[int] = None
    token_usage: Optional[Dict[str, int]] = None
    failure_classification: str = ""
    validation_results: Dict[str, str] = dc_field(default_factory=dict)
    disposition: str = "pending"


# ── §15.1 Script and turns ───────────────────────────────────────────


@dataclass
class ScriptTurn:
    """§15.1. One speaking turn."""
    id: str = dc_field(default_factory=_new_id)
    sequence: int = 0
    persona_id: str = ""
    text: str = ""
    speech_act: SpeechAct = SpeechAct.FACTUAL_REPORT
    claim_references: List[str] = dc_field(default_factory=list)
    evidence_references: List[str] = dc_field(default_factory=list)
    uncertainty_markers: List[str] = dc_field(default_factory=list)
    attribution: str = ""
    expected_duration_ms: Optional[int] = None
    epistemic_function: Optional[EpistemicFunction] = None
    clause_functions: List[Dict[str, Any]] = dc_field(default_factory=list)
    generation_metadata: Dict[str, Any] = dc_field(default_factory=dict)
    validation_status: ValidationOutcome = ValidationOutcome.REQUIRES_REVIEW

    @property
    def is_substantive(self) -> bool:
        """Transitional and transitional-humor turns carry no factual load.

        §15.2 applies labeling to *substantive* statements.
        """
        return self.speech_act not in (
            SpeechAct.TRANSITION,)


@dataclass
class BroadcastScript:
    """§15.1. A first-class persistent artifact.

    Never only an ephemeral string in application memory.
    """
    id: str = dc_field(default_factory=_new_id)
    segment_id: str = ""
    version: int = 1
    created_at: datetime = dc_field(default_factory=_utcnow)
    generation_request_id: str = ""
    evidence_packet_id: str = ""
    status: ValidationOutcome = ValidationOutcome.REQUIRES_REVIEW
    turns: List[ScriptTurn] = dc_field(default_factory=list)
    qualifications: List[str] = dc_field(default_factory=list)
    supersedes_script_id: Optional[str] = None
    world_snapshot_id: str = ""

    @property
    def substantive_turns(self) -> List[ScriptTurn]:
        return [t for t in self.turns if t.is_substantive]

    @property
    def all_claim_references(self) -> List[str]:
        return [c for t in self.turns for c in t.claim_references]


# ── §13.3 Segment ────────────────────────────────────────────────────


@dataclass
class Segment:
    """§13.3. Carries both editorial content and media state.

    content_state is the §13.4 distinction; media_generation_id identifies a
    synthesis attempt so obsolete chunks can be rejected (§13.5).
    """
    id: str = dc_field(default_factory=_new_id)
    session_id: str = ""
    story_id: str = ""
    plan_id: str = ""
    objective: str = ""
    status: SegmentStatus = SegmentStatus.PROPOSED
    content_state: ContentState = ContentState.GENERATED
    persona_id: str = ""
    script_id: Optional[str] = None
    media_generation_id: str = ""
    expected_duration_ms: Optional[int] = None
    interruption_id: Optional[str] = None
    completed_its_point: Optional[bool] = None
    resume_later: bool = False
    validation_outcome: Optional[ValidationOutcome] = None
    created_at: datetime = dc_field(default_factory=_utcnow)
    updated_at: datetime = dc_field(default_factory=_utcnow)

    def can_transition_to(self, target: SegmentStatus) -> bool:
        return target in SEGMENT_TRANSITIONS.get(self.status, set())

    def transition_to(self, target: SegmentStatus) -> None:
        """Refuse an illegal transition rather than warn about it.

        §13.3: a failed script validation must not enter the playback queue.
        """
        if not self.can_transition_to(target):
            raise SegmentTransitionError(
                f"illegal segment transition {self.status.value} -> "
                f"{target.value}")
        self.status = target
        self.updated_at = _utcnow()


# ── §13.2 Broadcast session ──────────────────────────────────────────


@dataclass
class BroadcastSession:
    """§13.2. Persisted often enough to support recovery after termination.

    In-memory buffers may be lost in a crash. The system must know which
    segments were generated, queued, started, and completed.
    """
    id: str = dc_field(default_factory=_new_id)
    started_at: datetime = dc_field(default_factory=_utcnow)
    stopped_at: Optional[datetime] = None
    operating_mode: str = "live"
    current_plan_id: Optional[str] = None
    current_segment_id: Optional[str] = None
    current_persona_id: Optional[str] = None
    playback_state: PlaybackState = PlaybackState.IDLE
    session_sequence: int = 0
    last_completed_segment_id: Optional[str] = None
    pending_interruption_id: Optional[str] = None
    metadata: Dict[str, Any] = dc_field(default_factory=dict)

    def next_sequence(self) -> int:
        self.session_sequence += 1
        return self.session_sequence


# ── §16.4–16.5 Audio chunks ──────────────────────────────────────────


@dataclass
class AudioChunk:
    """§16.5. Every chunk is bound to a segment and a synthesis attempt.

    The binding is what makes stale-chunk rejection possible: when a segment
    is cancelled or superseded, its obsolete generation's chunks must not be
    delivered as if current (§13.5).
    """
    id: str = dc_field(default_factory=_new_id)
    segment_id: str = ""
    script_turn_id: str = ""
    synthesis_attempt: str = ""
    session_id: str = ""
    sequence: int = 0
    data: bytes = b""
    sample_rate: int = 24000
    channels: int = 1
    sample_format: str = "pcm_s16le"
    duration_ms: Optional[int] = None
    is_end_of_segment: bool = False
    created_at: datetime = dc_field(default_factory=_utcnow)

    def is_stale_for(self, segment: Segment) -> bool:
        return self.synthesis_attempt != segment.media_generation_id


@dataclass
class AudioArtifact:
    """§16.7. Optional retention with a configurable policy.

    Transcripts and provenance can outlive large audio files.
    """
    id: str = dc_field(default_factory=_new_id)
    segment_id: str = ""
    script_version: int = 1
    tts_provider_id: str = ""
    voice_profile_version: str = ""
    audio_format: str = ""
    content_hash: str = ""
    duration_ms: Optional[int] = None
    created_at: datetime = dc_field(default_factory=_utcnow)
    retention_policy: str = "configurable"
    expires_at: Optional[datetime] = None
    storage_path: str = ""
