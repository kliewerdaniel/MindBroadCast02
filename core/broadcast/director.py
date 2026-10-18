"""The Broadcast Director.

Spec §13. The editorial planner decides what should happen; the director
manages the transition from that plan to speech.

§13.1 is explicit that the director "must be stateful, but it should not own
the underlying world model or independently redefine editorial policy." So
this module owns session state, the segment lifecycle, and the speech queue —
and nothing else. It does not rank stories, choose topics, or write scripts.

The property that matters most: a failed TTS request does not invalidate the
underlying evidence or require a new script (§13.3). The segment returns to
APPROVED and the media subsystem retries.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .entities import (
    AudioChunk, BroadcastSession, ContentState, InterruptionAction,
    PlaybackState, Segment, SegmentStatus, SegmentTransitionError,
    ValidationOutcome,
)


@dataclass
class Interruption:
    """§13.5 / §11.6. A record of an interruption, not just a flag.

    An interruption must not discard the current segment's history. The system
    records where it stopped, whether it completed its intended point, and
    whether the subject should be resumed later.
    """
    id: str
    segment_id: str
    action: InterruptionAction
    reason: str = ""
    stopped_at_sequence: int = 0
    completed_its_point: Optional[bool] = None
    resume_later: bool = False
    abandoned_audio_chunks: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class DirectorError(Exception):
    pass


class SegmentNotFound(DirectorError):
    pass


class InvalidState(DirectorError):
    pass


class BroadcastDirector:
    """§13. Stateful controller of one broadcast session."""

    def __init__(self, session: Optional[BroadcastSession] = None):
        self.session = session or BroadcastSession()
        self.segments: Dict[str, Segment] = {}
        self.queue: List[str] = []               # segment ids in play order
        self.interruptions: Dict[str, Interruption] = {}
        # Chunks keyed by segment id; each entry is that segment's generation.
        self.audio: Dict[str, List[AudioChunk]] = {}
        self.history: List[Dict[str, Any]] = []

    # ── §13.2 session state ───────────────────────────────────────────

    def persistable_state(self) -> Dict[str, Any]:
        """§13.2. The state needed to recover after process termination.

        In-memory buffers may be lost in a crash. This is what must survive:
        which segments exist, which were generated, queued, started, completed.
        """
        return {
            "session": {
                "id": self.session.id,
                "started_at": self.session.started_at.isoformat(),
                "operating_mode": self.session.operating_mode,
                "session_sequence": self.session.session_sequence,
                "current_segment_id": self.session.current_segment_id,
                "current_persona_id": self.session.current_persona_id,
                "playback_state": self.session.playback_state.value,
                "last_completed_segment_id":
                    self.session.last_completed_segment_id,
                "pending_interruption_id":
                    self.session.pending_interruption_id,
            },
            "segments": [
                {
                    "id": s.id,
                    "status": s.status.value,
                    "content_state": s.content_state.value,
                    "media_generation_id": s.media_generation_id,
                    "objective": s.objective,
                    "completed_its_point": s.completed_its_point,
                    "resume_later": s.resume_later,
                }
                for s in self.segments.values()
            ],
            "queue": list(self.queue),
        }

    @classmethod
    def recover(cls, state: Dict[str, Any]) -> "BroadcastDirector":
        """§13.2. Rebuild a director from persisted state.

        A segment that was PLAYING when the process died is not silently
        marked complete. It is recovered as INTERRUPTED, because the system
        does not know whether the listener heard the end.
        """
        session_data = state.get("session", {})
        session = BroadcastSession(
            id=session_data.get("id", ""),
            operating_mode=session_data.get("operating_mode", "live"),
            session_sequence=session_data.get("session_sequence", 0),
            current_segment_id=session_data.get("current_segment_id"),
            current_persona_id=session_data.get("current_persona_id"),
            playback_state=PlaybackState(
                session_data.get("playback_state", "idle")),
            last_completed_segment_id=session_data.get(
                "last_completed_segment_id"),
            pending_interruption_id=session_data.get("pending_interruption_id"),
        )
        director = cls(session)
        for segment_data in state.get("segments", []):
            status = SegmentStatus(segment_data["status"])
            if status is SegmentStatus.PLAYING:
                status = SegmentStatus.INTERRUPTED
            segment = Segment(
                id=segment_data["id"],
                status=status,
                content_state=ContentState(segment_data["content_state"]),
                media_generation_id=segment_data.get("media_generation_id", ""),
                objective=segment_data.get("objective", ""),
                completed_its_point=segment_data.get("completed_its_point"),
                resume_later=segment_data.get("resume_later", False),
            )
            director.segments[segment.id] = segment
        director.queue = list(state.get("queue", []))
        return director

    # ── segment lifecycle ─────────────────────────────────────────────

    def add_segment(self, segment: Segment) -> str:
        self.segments[segment.id] = segment
        self.history.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "event": "segment_proposed",
            "segment_id": segment.id,
        })
        return segment.id

    def get_segment(self, segment_id: str) -> Segment:
        segment = self.segments.get(segment_id)
        if segment is None:
            raise SegmentNotFound(segment_id)
        return segment

    def advance(self, segment_id: str, target: SegmentStatus) -> None:
        """Transition a segment, refusing illegal moves.

        §13.3: a failed script validation must not enter the playback queue.
        """
        segment = self.get_segment(segment_id)
        if not segment.can_transition_to(target):
            raise SegmentTransitionError(
                f"illegal segment transition {segment.status.value} -> "
                f"{target.value}")
        segment.transition_to(target)
        self.history.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "event": f"segment_{target.value}",
            "segment_id": segment_id,
        })

    def mark_validated(self, segment_id: str,
                       outcome: ValidationOutcome) -> None:
        """Record a validation result and move the segment accordingly.

        A REJECTED outcome is terminal. REQUIRES_REGENERATION returns the
        segment to generation. Approval moves it to synthesis.
        """
        segment = self.get_segment(segment_id)
        segment.validation_outcome = outcome
        if outcome is ValidationOutcome.REJECTED:
            self.advance(segment_id, SegmentStatus.REJECTED)
        elif outcome is ValidationOutcome.REQUIRES_REGENERATION:
            self.advance(segment_id, SegmentStatus.GENERATING)
        elif outcome is ValidationOutcome.REQUIRES_REVIEW:
            self.advance(segment_id, SegmentStatus.VALIDATING)
        else:
            self.advance(segment_id, SegmentStatus.APPROVED)

    # ── §13.3 media failure does not invalidate the script ────────────

    def record_synthesis_failure(self, segment_id: str) -> None:
        """A TTS failure returns the segment to APPROVED for retry.

        §13.3: the segment can remain approved while the media subsystem
        retries synthesis. The evidence and the script are untouched.
        """
        segment = self.get_segment(segment_id)
        if segment.status is not SegmentStatus.SYNTHESIZING:
            raise InvalidState(
                f"synthesis failure outside SYNTHESIZING: {segment.status.value}")
        self.advance(segment_id, SegmentStatus.FAILED)
        self.advance(segment_id, SegmentStatus.APPROVED)

    def retry_synthesis(self, segment_id: str,
                        new_generation_id: str) -> None:
        segment = self.get_segment(segment_id)
        if segment.status is not SegmentStatus.APPROVED:
            raise InvalidState(
                f"cannot retry synthesis from {segment.status.value}")
        segment.media_generation_id = new_generation_id
        self.advance(segment_id, SegmentStatus.SYNTHESIZING)

    # ── queue management ──────────────────────────────────────────────

    def enqueue(self, segment_id: str) -> None:
        """Only READY segments may enter the queue.

        §13.3: an unvalidated or unsynthesized segment must not enter the
        playback queue.
        """
        segment = self.get_segment(segment_id)
        if segment.status is not SegmentStatus.READY:
            raise InvalidState(
                f"only READY segments may be queued, got {segment.status.value}")
        if segment_id not in self.queue:
            self.queue.append(segment_id)
            self.advance(segment_id, SegmentStatus.QUEUED)

    def next_segment_id(self) -> Optional[str]:
        return self.queue[0] if self.queue else None

    def begin_playback(self, segment_id: str) -> None:
        segment = self.get_segment(segment_id)
        if segment.status is not SegmentStatus.QUEUED:
            raise InvalidState(
                f"cannot play from {segment.status.value}")
        self.session.current_segment_id = segment_id
        self.session.current_persona_id = segment.persona_id
        self.session.playback_state = PlaybackState.PLAYING
        segment.content_state = ContentState.DELIVERED
        self.advance(segment_id, SegmentStatus.PLAYING)

    def complete_playback(self, segment_id: str) -> None:
        segment = self.get_segment(segment_id)
        if segment.status is not SegmentStatus.PLAYING:
            raise InvalidState(
                f"cannot complete from {segment.status.value}")
        self.advance(segment_id, SegmentStatus.COMPLETED)
        if segment_id in self.queue:
            self.queue.remove(segment_id)
        self.session.last_completed_segment_id = segment_id
        self.session.playback_state = PlaybackState.IDLE

    def cancel(self, segment_id: str) -> None:
        """Cancel a segment before delivery, invalidating its audio.

        §13.5: chunks belonging to an obsolete generation must not be
        delivered as if they were current.
        """
        segment = self.get_segment(segment_id)
        if segment.status in (SegmentStatus.COMPLETED,):
            raise InvalidState("cannot cancel a completed segment")
        self.audio.pop(segment_id, None)
        self.advance(segment_id, SegmentStatus.CANCELLED)
        if segment_id in self.queue:
            self.queue.remove(segment_id)

    def supersede(self, segment_id: str,
                  new_generation_id: str) -> None:
        """Replace a segment's generation; the old chunks become stale."""
        segment = self.get_segment(segment_id)
        segment.media_generation_id = new_generation_id
        self.audio.pop(segment_id, None)
        if segment.status not in (SegmentStatus.QUEUED, SegmentStatus.APPROVED):
            raise InvalidState(
                f"cannot supersede from {segment.status.value}")
        if segment.status is SegmentStatus.QUEUED:
            self.queue.remove(segment_id)
        self.advance(segment_id, SegmentStatus.SUPERSEDED)

    # ── §11.6 interruption policy ─────────────────────────────────────

    def evaluate_interruption(
        self,
        materiality: float = 0.0,
        urgency: float = 0.0,
        evidence_quality: float = 0.0,
        public_safety_relevance: float = 0.0,
        is_verified: bool = True,
        current_segment_playback_progress: float = 1.0,
    ) -> InterruptionAction:
        """§11.6. Three actions; constant interruption destroys coherence.

        INTERRUPT is reserved for urgent, sufficiently supported, verified
        developments with public-safety relevance. Everything else continues or
        queues.
        """
        if not is_verified:
            # §11.6: whether the new information is sufficiently verified.
            return InterruptionAction.QUEUE_NEXT
        if (public_safety_relevance >= 0.8
                and urgency >= 0.8
                and evidence_quality >= 0.6
                and materiality >= 0.6):
            return InterruptionAction.INTERRUPT
        if materiality >= 0.4 or evidence_quality >= 0.6:
            return InterruptionAction.QUEUE_NEXT
        return InterruptionAction.CONTINUE

    def interrupt(
        self,
        segment_id: str,
        reason: str = "",
        stopped_at_sequence: int = 0,
        completed_its_point: Optional[bool] = None,
        resume_later: bool = False,
    ) -> Interruption:
        """§13.5. Abandon the current segment and record what happened.

        The abandoned audio is retained as a record but dropped from the
        delivery set, so it cannot play after the interruption.
        """
        segment = self.get_segment(segment_id)
        if segment.status is not SegmentStatus.PLAYING:
            raise InvalidState(
                f"cannot interrupt from {segment.status.value}")

        abandoned = [c.id for c in self.audio.get(segment_id, [])
                     if not c.is_end_of_segment]
        self.audio.pop(segment_id, None)

        interruption = Interruption(
            id=f"int-{segment_id}",
            segment_id=segment_id,
            action=InterruptionAction.INTERRUPT,
            reason=reason,
            stopped_at_sequence=stopped_at_sequence,
            completed_its_point=completed_its_point,
            resume_later=resume_later,
            abandoned_audio_chunks=abandoned,
        )
        self.interruptions[interruption.id] = interruption

        segment.completed_its_point = completed_its_point
        segment.resume_later = resume_later
        segment.interruption_id = interruption.id
        self.session.pending_interruption_id = interruption.id
        self.session.playback_state = PlaybackState.INTERRUPTED
        self.advance(segment_id, SegmentStatus.INTERRUPTED)
        if segment_id in self.queue:
            self.queue.remove(segment_id)
        return interruption

    def resume_interrupted(self, segment_id: str) -> None:
        """§11.6: an interrupted segment may be resumed, but never silently.

        It re-enters PLAYING only after an explicit resume call, and the
        interruption record remains.
        """
        segment = self.get_segment(segment_id)
        if segment.status is not SegmentStatus.INTERRUPTED:
            raise InvalidState(
                f"cannot resume from {segment.status.value}")
        self.session.pending_interruption_id = None
        self.advance(segment_id, SegmentStatus.PLAYING)

    # ── audio ─────────────────────────────────────────────────────────

    def attach_audio(self, segment_id: str, chunk: AudioChunk) -> None:
        segment = self.get_segment(segment_id)
        if chunk.is_stale_for(segment):
            raise InvalidState(
                f"stale chunk from generation {chunk.synthesis_attempt}, "
                f"segment is on {segment.media_generation_id}")
        self.audio.setdefault(segment_id, []).append(chunk)
        self.audio[segment_id].sort(key=lambda c: c.sequence)

    def deliverable_chunks(self, segment_id: str) -> List[AudioChunk]:
        """Chunks safe to deliver: current generation only (§13.5)."""
        segment = self.get_segment(segment_id)
        return [c for c in self.audio.get(segment_id, [])
                if not c.is_stale_for(segment)
                and segment.status in (
                    SegmentStatus.QUEUED, SegmentStatus.PLAYING,
                    SegmentStatus.COMPLETED)]
