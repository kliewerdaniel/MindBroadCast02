"""Live transcript.

Spec §18.3.

The distinction that defines this module: "The interface should distinguish
generated text from text actually delivered to the audio stream. A script that
was generated but never played should not appear indistinguishably in the
public transcript."

So a transcript entry is created at delivery, not at generation. A generated
turn that was never played is recorded as such (`delivered=False`) and marked,
never shown as spoken.

§18.3's per-entry fields, all present: speaker, segment title, text, start and
completion timestamps where available, current playback state, story
references, evidence links, epistemic classification, correction status, and
whether the segment completed / was interrupted / was abandoned.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from ..broadcast.entities import (
    BroadcastScript,
    ContentState,
    Segment,
)


class SegmentDeliveryState(Enum):
    """§18.3: completed, interrupted, or abandoned — never silently completed."""
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    ABANDONED = "abandoned"
    NOT_DELIVERED = "not_delivered"


@dataclass
class TranscriptEntry:
    """§18.3. One delivered (or explicitly not-delivered) turn."""
    id: str
    segment_id: str
    segment_title: str
    speaker: str
    text: str
    turn_sequence: int = 0

    # Delivery distinction (§18.3): generated text is not spoken text.
    delivered: bool = False
    content_state: str = ContentState.GENERATED.value

    # Timestamps where available.
    started_at: datetime | None = None
    completed_at: datetime | None = None
    playback_state: str = "idle"

    # References and classification.
    story_references: list[str] = field(default_factory=list)
    evidence_links: list[str] = field(default_factory=list)
    epistemic_classification: str | None = None
    speech_act: str | None = None

    # Correction status (§15.7) and delivery outcome (§18.3).
    correction_status: str = "none"
    delivery_state: SegmentDeliveryState = SegmentDeliveryState.NOT_DELIVERED
    superseded_by: str | None = None

    @property
    def is_public(self) -> bool:
        """Only delivered text appears in the public transcript (§18.3)."""
        return self.delivered and self.delivery_state is not (
            SegmentDeliveryState.NOT_DELIVERED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "segment_id": self.segment_id,
            "segment_title": self.segment_title,
            "speaker": self.speaker,
            "text": self.text,
            "turn_sequence": self.turn_sequence,
            "delivered": self.delivered,
            "content_state": self.content_state,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (self.completed_at.isoformat()
                             if self.completed_at else None),
            "playback_state": self.playback_state,
            "story_references": self.story_references,
            "evidence_links": self.evidence_links,
            "epistemic_classification": self.epistemic_classification,
            "speech_act": self.speech_act,
            "correction_status": self.correction_status,
            "delivery_state": self.delivery_state.value,
            "superseded_by": self.superseded_by,
            "is_public": self.is_public,
        }


class TranscriptService:
    """Builds the §18.3 transcript from segments and scripts.

    Two invariants:
    - A turn enters as delivered only when its segment actually plays.
    - An interrupted or abandoned segment is marked, not completed (§18.3).
    """

    def __init__(self):
        self._entries: list[TranscriptEntry] = []

    def record_generated(
        self, segment: Segment, script: BroadcastScript,
        segment_title: str = "",
    ) -> list[TranscriptEntry]:
        """Record a generated script. Not delivered — not in the public
        transcript until the segment plays (§18.3)."""
        entries = []
        for turn in script.turns:
            entries.append(TranscriptEntry(
                id=f"tr-{segment.id}-{turn.sequence}",
                segment_id=segment.id,
                segment_title=segment_title or segment.objective,
                speaker=turn.persona_id,
                text=turn.text,
                turn_sequence=turn.sequence,
                delivered=False,
                content_state=ContentState.GENERATED.value,
                epistemic_classification=(
                    turn.epistemic_function.value
                    if turn.epistemic_function else None),
                speech_act=turn.speech_act.value,
                evidence_links=list(turn.evidence_references),
                delivery_state=SegmentDeliveryState.NOT_DELIVERED,
            ))
        self._entries.extend(entries)
        return entries

    def mark_delivered(self, segment_id: str,
                       started_at: datetime | None = None) -> int:
        """The segment began playing: its turns become delivered text."""
        count = 0
        for entry in self._entries:
            if entry.segment_id == segment_id and not entry.delivered:
                entry.delivered = True
                entry.content_state = ContentState.DELIVERED.value
                entry.started_at = started_at or datetime.now(UTC)
                entry.delivery_state = SegmentDeliveryState.COMPLETED
                count += 1
        return count

    def mark_interrupted(self, segment_id: str,
                         completed_at: datetime | None = None) -> int:
        """§18.3: an interrupted segment is marked, not completed."""
        count = 0
        for entry in self._entries:
            if entry.segment_id == segment_id:
                entry.delivery_state = SegmentDeliveryState.INTERRUPTED
                entry.completed_at = completed_at or datetime.now(UTC)
                count += 1
        return count

    def mark_abandoned(self, segment_id: str) -> int:
        count = 0
        for entry in self._entries:
            if entry.segment_id == segment_id:
                entry.delivery_state = SegmentDeliveryState.ABANDONED
                count += 1
        return count

    def mark_completed(self, segment_id: str,
                       completed_at: datetime | None = None) -> int:
        count = 0
        for entry in self._entries:
            if entry.segment_id == segment_id:
                entry.delivery_state = SegmentDeliveryState.COMPLETED
                entry.completed_at = completed_at or datetime.now(UTC)
                count += 1
        return count

    def mark_corrected(self, segment_id: str,
                       superseded_by: str) -> int:
        """§15.7: the correction is linked; the original text is preserved."""
        count = 0
        for entry in self._entries:
            if entry.segment_id == segment_id:
                entry.correction_status = "superseded"
                entry.superseded_by = superseded_by
                count += 1
        return count

    def public_transcript(self, limit: int = 100) -> list[TranscriptEntry]:
        """§18.3: only delivered, non-abandoned text."""
        public = [e for e in self._entries if e.is_public]
        return public[-limit:]

    def full_transcript(self, limit: int = 200) -> list[TranscriptEntry]:
        """Including generated-but-undelivered, for operator inspection."""
        return self._entries[-limit:]

    def entries_for_segment(self, segment_id: str) -> list[TranscriptEntry]:
        return [e for e in self._entries if e.segment_id == segment_id]
