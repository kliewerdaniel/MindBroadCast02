"""External streaming boundary.

Spec §16.8. "The audio transport and metadata transport should be logically
separate." "A later deployment may add Icecast or another streaming-radio
service without requiring changes to the editorial engine."

So this module defines the seam, not the server. An `AudioTransport` is a
pluggable sink the audio buffer writes to; the editorial engine and the buffer
are unchanged whether the sink is a browser HTTP stream or an Icecast mount.

The separation is the requirement. A design that let metadata ride on the audio
channel, or that coupled the editorial engine to a specific streaming server,
would fail §16.8. Both transports here implement the same interface and neither
imports the other.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


class TransportKind(Enum):
    HTTP_CHUNKED = "http_chunked"          # browser live stream (§19.4)
    ICECAST = "icecast"                    # optional external radio (§16.8)
    SSE_METADATA = "sse_metadata"          # metadata only, never audio (§19.4)


@dataclass
class StreamEndpoint:
    """§19.4: the audio endpoint must define its contract explicitly."""
    kind: TransportKind
    path: str = ""
    audio_format: str = "pcm_s16le"
    sample_rate: int = 24000
    channels: int = 1
    framing: str = "length-prefixed-chunks"
    buffering: str = "bounded-buffer-with-backpressure"
    reconnection: str = "resume-at-segment-boundary"
    end_of_stream: str = "explicit-eos-marker"
    authentication: str = "required-for-admin"
    backpressure_policy: str = "bounded-buffer-then-disconnect"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "path": self.path,
            "audio_format": self.audio_format,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "framing": self.framing,
            "buffering": self.buffering,
            "reconnection": self.reconnection,
            "end_of_stream": self.end_of_stream,
            "authentication": self.authentication,
            "backpressure_policy": self.backpressure_policy,
        }


@runtime_checkable
class AudioTransport(Protocol):
    """A pluggable audio sink (§16.8)."""

    kind: TransportKind

    def contract(self) -> StreamEndpoint: ...

    def open(self) -> bool: ...

    def write_chunk(self, chunk: bytes, sequence: int) -> bool: ...

    def close(self) -> None: ...


class HttpChunkedTransport:
    """The initial browser transport (§19.4, §16.8)."""

    kind = TransportKind.HTTP_CHUNKED

    def __init__(self):
        self.opened = False
        self.chunks_written = 0
        self.closed = False

    def contract(self) -> StreamEndpoint:
        return StreamEndpoint(
            kind=self.kind, path="/api/v1/audio/live",
            framing="chunked-http-length-prefixed",
        )

    def open(self) -> bool:
        self.opened = True
        return True

    def write_chunk(self, chunk: bytes, sequence: int) -> bool:
        if not self.opened or self.closed:
            return False
        self.chunks_written += 1
        return True

    def close(self) -> None:
        self.closed = True


class IcecastTransport:
    """The optional external radio sink (§16.8).

    A stub: it records that it was opened and written to, so the seam is
    testable without running a real Icecast server. The point is that adding
    it requires no change to the editorial engine or the audio buffer.
    """

    kind = TransportKind.ICECAST

    def __init__(self, mount_point: str = "/mindbroadcast.mp3",
                 host: str = "localhost", port: int = 8000):
        self.mount_point = mount_point
        self.host = host
        self.port = port
        self.opened = False
        self.chunks_written = 0
        self.closed = False

    def contract(self) -> StreamEndpoint:
        return StreamEndpoint(
            kind=self.kind, path=self.mount_point,
            audio_format="mp3", framing="icecast-source-protocol",
            reconnection="reconnect-to-live-position",
        )

    def open(self) -> bool:
        self.opened = True
        return True

    def write_chunk(self, chunk: bytes, sequence: int) -> bool:
        if not self.opened or self.closed:
            return False
        self.chunks_written += 1
        return True

    def close(self) -> None:
        self.closed = True


class MetadataTransport:
    """§19.4: SSE carries metadata and control events, never audio.

    Kept as a separate type so the audio and metadata paths cannot be confused.
    """

    kind = TransportKind.SSE_METADATA

    def __init__(self):
        self.events_sent = 0

    def contract(self) -> StreamEndpoint:
        return StreamEndpoint(
            kind=self.kind, path="/api/v1/events",
            framing="server-sent-events",
        )

    def send_event(self, event_dict: Dict[str, Any]) -> bool:
        self.events_sent += 1
        return True


class StreamManager:
    """§16.8. Writes audio to a pluggable transport; metadata stays separate.

    The editorial engine is untouched by which transport is active — that is
    the seam.
    """

    def __init__(self, audio_transport: Optional[AudioTransport] = None,
                 metadata_transport: Optional[MetadataTransport] = None):
        self.audio_transport = audio_transport or HttpChunkedTransport()
        self.metadata_transport = metadata_transport or MetadataTransport()

    def open(self) -> bool:
        return self.audio_transport.open()

    def deliver_chunk(self, chunk: bytes, sequence: int) -> bool:
        return self.audio_transport.write_chunk(chunk, sequence)

    def emit_metadata(self, event: Dict[str, Any]) -> bool:
        """Metadata rides its own channel, never the audio stream (§19.4)."""
        return self.metadata_transport.send_event(event)

    def close(self) -> None:
        self.audio_transport.close()

    def transports_are_separate(self) -> bool:
        """§16.8: audio and metadata are logically separate."""
        return (self.audio_transport.kind is not TransportKind.SSE_METADATA
                and self.metadata_transport.kind is TransportKind.SSE_METADATA)

    def swap_audio_transport(self, transport: AudioTransport) -> None:
        """§16.8: an external server can be added without changing the engine."""
        self.audio_transport.close()
        self.audio_transport = transport
        transport.open()
