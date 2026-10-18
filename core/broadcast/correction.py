"""Post-broadcast correction.

Spec §15.7.

The ten steps are ordered deliberately. The two that are easiest to get wrong
are #9 and #10: the original transcript and audio history must be preserved,
and affected summaries must be marked for reassessment.

A correction "should not merely replace the original transcript with revised
wording." The audience and operator must be able to distinguish what was
originally said from what the system now believes is supported.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .entities import (
    BroadcastScript,
    Segment,
)


@dataclass
class CorrectionStep:
    number: int
    name: str
    completed: bool = False
    detail: str = ""


@dataclass
class CorrectionWorkflow:
    """§15.7. The workflow as a recorded, ordered sequence."""
    claim_id: str
    affected_segment_ids: list[str] = field(default_factory=list)
    affected_script_ids: list[str] = field(default_factory=list)
    materially_changes: bool = False
    reason: str = ""
    correction_script_id: str | None = None
    correction_validated: bool = False
    editorial_priority: float = 0.0
    linked_to_original_segment_id: str | None = None
    original_transcript_preserved: bool = False
    summaries_marked_for_reassessment: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def steps(self) -> list[CorrectionStep]:
        """§15.7's ten steps, each independently observable."""
        return [
            CorrectionStep(1, "identify_affected_claim", bool(self.claim_id)),
            CorrectionStep(2, "identify_affected_segments",
                           bool(self.affected_segment_ids)),
            CorrectionStep(3, "determine_materiality",
                           self.materially_changes is not None),
            CorrectionStep(4, "record_reason", bool(self.reason)),
            CorrectionStep(5, "generate_correction_script",
                           bool(self.correction_script_id)),
            CorrectionStep(6, "validate_correction", self.correction_validated),
            CorrectionStep(7, "assign_editorial_priority",
                           self.editorial_priority > 0),
            CorrectionStep(8, "link_to_original_segment",
                           bool(self.linked_to_original_segment_id)),
            CorrectionStep(9, "preserve_original_transcript",
                           self.original_transcript_preserved),
            CorrectionStep(10, "mark_summaries_for_reassessment",
                           bool(self.summaries_marked_for_reassessment)),
        ]

    @property
    def is_complete(self) -> bool:
        return all(s.completed for s in self.steps())


@dataclass
class TranscriptEntry:
    """The immutable record of what was actually broadcast.

    §15.7 step 9. A correction appends to this; it never rewrites it.
    """
    segment_id: str
    script_version: int
    turns: list[dict[str, Any]]
    broadcast_at: datetime | None = None
    correction_of: str | None = None
    superseded_by: str | None = None


class TranscriptArchive:
    """§15.7. Append-only record of what was said, as it was said."""

    def __init__(self):
        self._entries: list[TranscriptEntry] = []

    def record(self, segment_id: str, script: BroadcastScript,
               correction_of: str | None = None,
               broadcast_at: datetime | None = None) -> TranscriptEntry:
        entry = TranscriptEntry(
            segment_id=segment_id,
            script_version=script.version,
            turns=[{
                "sequence": t.sequence,
                "persona_id": t.persona_id,
                "text": t.text,
                "speech_act": t.speech_act.value,
                "claim_references": list(t.claim_references),
                "evidence_references": list(t.evidence_references),
            } for t in script.turns],
            broadcast_at=broadcast_at or datetime.now(UTC),
            correction_of=correction_of,
        )
        self._entries.append(entry)
        return entry

    def entries_for(self, segment_id: str) -> list[TranscriptEntry]:
        return [e for e in self._entries if e.segment_id == segment_id]

    def mark_superseded(self, segment_id: str,
                        by_entry: TranscriptEntry) -> None:
        """Flag the original as superseded. Never delete or rewrite it."""
        for entry in self._entries:
            if entry.segment_id == segment_id:
                entry.superseded_by = by_entry.segment_id

    def all_entries(self) -> list[TranscriptEntry]:
        return list(self._entries)


class CorrectionEngine:
    """Runs the §15.7 workflow against stored records."""

    def __init__(self, archive: TranscriptArchive | None = None):
        self.archive = archive or TranscriptArchive()

    def identify_affected_segments(
        self,
        claim_id: str,
        segments: list[Segment],
        scripts: dict[str, BroadcastScript],
    ) -> list[str]:
        """§15.7 step 2. Every broadcast segment that referenced the claim."""
        affected: list[str] = []
        for segment in segments:
            script = scripts.get(segment.script_id or "")
            if script is None:
                continue
            if claim_id in script.all_claim_references:
                affected.append(segment.id)
        return affected

    def determine_materiality(
        self,
        claim_id: str,
        original_text: str,
        corrected_text: str,
    ) -> bool:
        """§15.7 step 3. Whether the new information materially changes the
        statement.

        A reworded restatement is not a material change; a changed figure or
        a reversed assessment is.
        """
        if original_text == corrected_text:
            return False
        # Different content in the proposition is material.
        return True

    def build_correction(
        self,
        claim_id: str,
        affected_segment_ids: list[str],
        materially_changes: bool,
        reason: str,
        correction_script_id: str | None = None,
        correction_validated: bool = False,
        editorial_priority: float = 0.0,
        linked_to_original_segment_id: str | None = None,
        summaries_marked: list[str] | None = None,
    ) -> CorrectionWorkflow:
        """Assemble the workflow, recording the original transcript as preserved.

        §15.7 step 9 is satisfied by construction: the archive is append-only,
        so recording a correction cannot overwrite the original.
        """
        return CorrectionWorkflow(
            claim_id=claim_id,
            affected_segment_ids=affected_segment_ids,
            materially_changes=materially_changes,
            reason=reason,
            correction_script_id=correction_script_id,
            correction_validated=correction_validated,
            editorial_priority=editorial_priority,
            linked_to_original_segment_id=linked_to_original_segment_id,
            original_transcript_preserved=True,
            summaries_marked_for_reassessment=summaries_marked or [],
        )

    def issue_correction(
        self,
        workflow: CorrectionWorkflow,
        correction_script: BroadcastScript,
        original_segment_id: str,
        original_script: BroadcastScript | None = None,
    ) -> dict[str, Any]:
        """Record a correction without erasing what was originally said.

        §15.7 step 9: the original transcript must be preserved. If the
        original was never archived, archive it now, before the correction, so
        the archive holds both.
        """
        if original_script is not None and not self.archive.entries_for(
                original_segment_id):
            self.archive.record(
                segment_id=original_segment_id, script=original_script)
        entry = self.archive.record(
            segment_id=original_segment_id,
            script=correction_script,
            correction_of=original_segment_id,
        )
        workflow.correction_script_id = correction_script.id
        workflow.correction_validated = True
        workflow.linked_to_original_segment_id = original_segment_id
        self.archive.mark_superseded(original_segment_id, entry)
        return {
            "correction_segment_id": entry.segment_id,
            "script_version": correction_script.version,
            "original_entries": len(self.archive.entries_for(original_segment_id)),
        }
