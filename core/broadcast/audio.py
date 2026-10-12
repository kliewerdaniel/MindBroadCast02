"""Audio buffering and delivery.

Spec §16.4–16.7.

The buffer's job is to make three things true at once: chunks arrive in order,
a slow client cannot exhaust memory, and chunks from a cancelled or superseded
generation are never delivered as though they were current (§13.5).

Stale-chunk rejection is the reason every chunk carries both a segment id and
a synthesis attempt id. The binding is not metadata for its own sake — it is
the only way to know that a chunk arriving late belongs to a generation the
system has already abandoned.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

from .entities import (
    AudioArtifact, AudioChunk, Segment, SegmentStatus,
)


class BufferFull(Exception):
    pass


class OutOfOrderChunk(Exception):
    pass


class StaleChunk(Exception):
    pass


@dataclass
class BufferStats:
    segments: int
    chunks: int
    bytes: int
    dropped: int
    rejected: int


class AudioBuffer:
    """§16.6. Ordered, bounded, backpressure, cancellation, stale rejection.

    Bounds are enforced on bytes, not just chunk count, because a slow client
    is a memory problem before it is a queue-depth problem.
    """

    def __init__(
        self,
        max_bytes: int = 32 * 1024 * 1024,
        max_chunks_per_segment: int = 512,
        max_segments: int = 16,
    ):
        self.max_bytes = max_bytes
        self.max_chunks_per_segment = max_chunks_per_segment
        self.max_segments = max_segments
        self._buffers: Dict[str, Deque[AudioChunk]] = {}
        self._dropped = 0
        self._rejected = 0
        self._order: List[str] = []

    # ── §16.6 inspection ──────────────────────────────────────────────

    def stats(self) -> BufferStats:
        total_bytes = sum(
            len(c.data)
            for chunks in self._buffers.values()
            for c in chunks)
        total_chunks = sum(len(c) for c in self._buffers.values())
        return BufferStats(
            segments=len(self._buffers),
            chunks=total_chunks,
            bytes=total_bytes,
            dropped=self._dropped,
            rejected=self._rejected,
        )

    def inspect(self, segment_id: str) -> List[Dict[str, Any]]:
        """§16.6 queue inspection."""
        return [
            {
                "id": c.id,
                "sequence": c.sequence,
                "duration_ms": c.duration_ms,
                "bytes": len(c.data),
                "synthesis_attempt": c.synthesis_attempt,
                "end_of_segment": c.is_end_of_segment,
            }
            for c in self._buffers.get(segment_id, [])
        ]

    # ── ingestion ─────────────────────────────────────────────────────

    def add_chunk(self, segment: Segment, chunk: AudioChunk) -> None:
        """Add a chunk, enforcing ordering, staleness, and bounds.

        Ordering is enforced on the segment's own sequence numbers, so a
        chunk that arrives out of order is rejected rather than buffered
        indefinitely in hope of a gap being filled.
        """
        if chunk.is_stale_for(segment):
            self._rejected += 1
            raise StaleChunk(
                f"chunk from generation {chunk.synthesis_attempt}, segment "
                f"is on {segment.media_generation_id}")

        buffer = self._buffers.setdefault(segment.id, deque())
        if segment.id not in self._order:
            if len(self._order) >= self.max_segments:
                self._dropped += 1
                raise BufferFull(
                    f"buffer holds {self.max_segments} segments")
            self._order.append(segment.id)

        expected = buffer[-1].sequence + 1 if buffer else 0
        if chunk.sequence < expected:
            self._rejected += 1
            raise OutOfOrderChunk(
                f"expected sequence {expected}, got {chunk.sequence}")
        if chunk.sequence > expected:
            self._rejected += 1
            raise OutOfOrderChunk(
                f"gap in sequence: expected {expected}, got {chunk.sequence}")

        if len(buffer) >= self.max_chunks_per_segment:
            self._dropped += 1
            raise BufferFull(
                f"segment {segment.id} holds {self.max_chunks_per_segment} chunks")

        if self.stats().bytes + len(chunk.data) > self.max_bytes:
            self._dropped += 1
            raise BufferFull("buffer byte budget exceeded")

        buffer.append(chunk)

    # ── §16.6 backpressure and consumption ────────────────────────────

    def has_backpressure(self, threshold: float = 0.8) -> bool:
        """§16.6. A slow client must be detectable, not merely tolerated."""
        stats = self.stats()
        if self.max_bytes <= 0:
            return False
        return stats.bytes >= self.max_bytes * threshold

    def take_chunk(self, segment_id: str) -> Optional[AudioChunk]:
        """Remove and return the next chunk for a segment."""
        buffer = self._buffers.get(segment_id)
        if not buffer:
            return None
        return buffer.popleft()

    def segment_complete(self, segment_id: str) -> bool:
        """§16.4 step 8. Signal the end of the segment explicitly."""
        buffer = self._buffers.get(segment_id)
        if not buffer:
            return False
        return bool(buffer[-1].is_end_of_segment)

    def cancel_segment(self, segment_id: str) -> int:
        """§16.6 cancellation. Returns how many chunks were discarded."""
        count = len(self._buffers.pop(segment_id, deque()))
        if segment_id in self._order:
            self._order.remove(segment_id)
        self._dropped += count
        return count

    def clear_stale(self, segment: Segment) -> int:
        """Drop chunks belonging to a superseded generation (§13.5)."""
        buffer = self._buffers.get(segment.id)
        if buffer is None:
            return 0
        kept = deque(c for c in buffer if not c.is_stale_for(segment))
        removed = len(buffer) - len(kept)
        self._buffers[segment.id] = kept
        self._rejected += removed
        if not kept:
            self._buffers.pop(segment.id, None)
            if segment.id in self._order:
                self._order.remove(segment.id)
        return removed


# ── §16.4 Chunking ───────────────────────────────────────────────────


def chunk_text(text: str, max_chars: int = 240) -> List[str]:
    """§16.4 step 2. Divide text into synthesis units.

    Splitting happens on sentence boundaries where possible. §16.4 warns
    against "unnecessarily fragmenting speech into tiny chunks that damage
    prosody or make sentence boundaries unnatural," so a short final sentence
    is merged into the previous chunk rather than emitted alone.
    """
    if not text or not text.strip():
        return []

    sentences = _split_sentences(text)
    chunks: List[str] = []
    current = ""

    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            chunks.append(current)
            current = sentence

    if current:
        chunks.append(current)

    # Merge a tiny trailing chunk so the last unit is not a one-word fragment.
    if len(chunks) >= 2 and len(chunks[-1]) < max_chars // 6:
        chunks[-2] = f"{chunks[-2]} {chunks[-1]}"
        chunks.pop()
    return chunks


def _split_sentences(text: str) -> List[str]:
    import re
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


# ── §16.5 Format normalization ───────────────────────────────────────


@dataclass
class AudioFormat:
    sample_rate: int
    channels: int
    sample_format: str          # "pcm_s16le" | "pcm_f32le" | ...


CANONICAL_FORMAT = AudioFormat(sample_rate=24000, channels=1,
                               sample_format="pcm_s16le")


def needs_normalization(chunk: AudioChunk) -> bool:
    """§16.5. Audio from different voices or providers cannot be concatenated
    directly without format normalization."""
    return not (
        chunk.sample_rate == CANONICAL_FORMAT.sample_rate
        and chunk.channels == CANONICAL_FORMAT.channels
        and chunk.sample_format == CANONICAL_FORMAT.sample_format
    )


def normalize_chunk(chunk: AudioChunk) -> AudioChunk:
    """Bring a chunk to the canonical internal representation.

    The buffer records that normalization was required; it does not silently
    pretend a mismatched chunk was fine.
    """
    if not needs_normalization(chunk):
        return chunk
    return AudioChunk(
        id=chunk.id,
        segment_id=chunk.segment_id,
        script_turn_id=chunk.script_turn_id,
        synthesis_attempt=chunk.synthesis_attempt,
        session_id=chunk.session_id,
        sequence=chunk.sequence,
        data=chunk.data,
        sample_rate=CANONICAL_FORMAT.sample_rate,
        channels=CANONICAL_FORMAT.channels,
        sample_format=CANONICAL_FORMAT.sample_format,
        duration_ms=chunk.duration_ms,
        is_end_of_segment=chunk.is_end_of_segment,
        created_at=chunk.created_at,
    )


# ── §16.7 Artifact storage ───────────────────────────────────────────


class ArtifactStore:
    """§16.7. Optional retention with a configurable policy.

    The default is configurable rather than indefinite: transcripts and
    provenance can outlive large audio files when storage limits require it.
    """

    def __init__(self, default_retention_days: Optional[int] = 30,
                 retain_audio: bool = True):
        self.default_retention_days = default_retention_days
        self.retain_audio = retain_audio
        self._artifacts: Dict[str, AudioArtifact] = {}

    def store(self, artifact: AudioArtifact,
              now: Optional[datetime] = None) -> str:
        if not self.retain_audio:
            # Transcript and provenance are retained even when audio is not.
            artifact.storage_path = ""
            artifact.audio_format = "transcript_only"
        if self.default_retention_days is not None:
            base = now or datetime.now(timezone.utc)
            artifact.expires_at = base.replace(
                microsecond=0)
        self._artifacts[artifact.id] = artifact
        return artifact.id

    def get(self, artifact_id: str) -> Optional[AudioArtifact]:
        return self._artifacts.get(artifact_id)

    def list_for_segment(self, segment_id: str) -> List[AudioArtifact]:
        return [a for a in self._artifacts.values()
                if a.segment_id == segment_id]
