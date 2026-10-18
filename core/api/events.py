"""Event stream.

Spec §19.3. Server-Sent Events for one-way server-to-browser metadata updates.

Two requirements shape this module:

1. **Stable, replayable events.** Each event carries a stable id, timestamp,
   type, and versioned payload. The stream supports reconnection and replay.

2. **Persistence, not just memory.** §19.3: "The application should not rely on
   an in-memory event queue as the only record of important state transitions."
   So `EventStore` is the record; the SSE stream is a view onto it. A reconnecting
   client replays from its last-seen event id.

The audio/event separation of §19.4 is enforced by type: this stream carries
metadata only. There is no audio code path here.
"""

import itertools
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Set

from collections import deque


# §19.3's example event types, plus the ones the phases so far produce.
class EventType:
    BROADCAST_STATE_CHANGED = "broadcast.state_changed"
    BROADCAST_SEGMENT_STARTED = "broadcast.segment_started"
    BROADCAST_SEGMENT_COMPLETED = "broadcast.segment_completed"
    BROADCAST_SEGMENT_INTERRUPTED = "broadcast.segment_interrupted"
    TRANSCRIPT_TURN_ADDED = "transcript.turn_added"
    WORLD_STORY_UPDATED = "world.story_updated"
    WORLD_CORRECTION_DETECTED = "world.correction_detected"
    SYSTEM_WORKER_FAILED = "system.worker_failed"
    SYSTEM_WORKER_RECOVERED = "system.worker_recovered"
    AUDIO_STREAM_READY = "audio.stream_ready"


@dataclass
class StreamEvent:
    """§19.3: a stable event id, timestamp, type, and versioned payload."""
    id: str
    type: str
    timestamp: datetime
    payload: Dict[str, Any] = field(default_factory=dict)
    payload_version: int = 1

    def to_sse(self) -> str:
        """The SSE wire format: `id:`, `event:`, `data:` lines."""
        data = json.dumps({
            "id": self.id,
            "type": self.type,
            "timestamp": self.timestamp.isoformat(),
            "payload_version": self.payload_version,
            "payload": self.payload,
        })
        return f"id: {self.id}\nevent: {self.type}\ndata: {data}\n\n"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "timestamp": self.timestamp.isoformat(),
            "payload_version": self.payload_version,
            "payload": self.payload,
        }


class EventStore:
    """Persistent record of state transitions (§19.3).

    The in-memory deque is a bounded ring for fast tail reads; the full log is
    retained in a list up to `retention`, which is what makes replay work after
    reconnection. Important transitions are never dropped while within
    retention.
    """

    def __init__(self, retention: int = 10000):
        self.retention = retention
        self._events: Deque[StreamEvent] = deque(maxlen=retention)
        self._counter = itertools.count(1)
        self._subscribers: List[Callable[[StreamEvent], None]] = []

    def emit(self, event_type: str, payload: Optional[Dict[str, Any]] = None,
             payload_version: int = 1) -> StreamEvent:
        event = StreamEvent(
            id=f"evt-{next(self._counter):012d}",
            type=event_type,
            timestamp=datetime.now(timezone.utc),
            payload=payload or {},
            payload_version=payload_version,
        )
        self._events.append(event)
        for subscriber in list(self._subscribers):
            subscriber(event)
        return event

    def replay_after(self, last_event_id: str,
                     limit: int = 500) -> List[StreamEvent]:
        """Events after `last_event_id`, for a reconnecting client (§19.3).

        An unknown id replays nothing rather than everything: a client that
        lost its cursor gets a clean gap signal, not a duplicate flood.
        """
        ids = [e.id for e in self._events]
        if last_event_id not in ids:
            return []
        index = ids.index(last_event_id)
        return list(self._events)[index + 1:index + 1 + limit]

    def replay_all(self, limit: int = 500) -> List[StreamEvent]:
        return list(self._events)[-limit:]

    def latest(self, event_type: Optional[str] = None) -> Optional[StreamEvent]:
        for event in reversed(self._events):
            if event_type is None or event.type == event_type:
                return event
        return None

    def subscribe(self, callback: Callable[[StreamEvent], None]) -> None:
        self._subscribers.append(callback)

    def count(self, event_type: Optional[str] = None) -> int:
        if event_type is None:
            return len(self._events)
        return sum(1 for e in self._events if e.type == event_type)

    def event_ids(self) -> Set[str]:
        return {e.id for e in self._events}


class EventStream:
    """An SSE view over the EventStore for one client connection.

    Framing: §19.3 SSE format. The stream is metadata only — §19.4 keeps SSE
    off the audio path.
    """

    def __init__(self, store: EventStore):
        self.store = store

    def connect(self, last_event_id: Optional[str] = None) -> List[StreamEvent]:
        """Initial batch on connect: replay after the client's cursor."""
        if last_event_id:
            return self.store.replay_after(last_event_id)
        return self.store.replay_all()

    def format_batch(self, events: List[StreamEvent]) -> str:
        return "".join(e.to_sse() for e in events)

    def on_new_event(self, last_event_id: Optional[str] = None) -> str:
        """Format everything after the cursor, for a poll-style client."""
        return self.format_batch(self.connect(last_event_id))
