"""Structured logging and health checks.

Spec §21.3 (structured, correlated, redacted logs) and §21.4 (liveness vs
readiness).

§21.3's privacy rule is the load-bearing constraint: "a model-generation failure
can usually be diagnosed using the request ID, model, error category, and
validation result without recording the full listener message or source text in
every log entry." So the `RedactingFormatter` strips content fields — diagnosis
uses identifiers, not payloads.

§21.4's distinction: liveness (the process functions) is not readiness (a
specific capability works). A single global `healthy: false` "would obscure that
distinction" — the readiness response must name degraded capabilities.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .metrics import HealthState, MetricsRegistry, Subsystem

# Fields that must never appear in a log payload (§21.3).
SENSITIVE_FIELDS = (
    "content", "message_content", "listener_message", "source_text",
    "article_text", "excerpt", "text", "prompt", "raw_prompt",
    "system_prompt", "password", "token", "api_key", "secret",
)


@dataclass
class LogRecord:
    """§21.3. Correlated across workflows."""
    level: str
    event: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    segment_id: str | None = None
    session_id: str | None = None
    correlation_id: str | None = None
    stage: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        payload = {
            "ts": self.timestamp.isoformat(),
            "level": self.level,
            "event": self.event,
        }
        for key in ("segment_id", "session_id", "correlation_id", "stage"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        payload.update(self.fields)
        return json.dumps(payload, default=str)


class RedactingFormatter:
    """§21.3. Strips sensitive content before a record is emitted.

    Diagnosis uses the request id, model, error category, and validation
    result — never the full message or source text.
    """

    REDACTED = "[redacted]"

    def __init__(self, sensitive_fields: tuple = SENSITIVE_FIELDS):
        self.sensitive_fields = set(f.lower() for f in sensitive_fields)

    def redact(self, record: LogRecord) -> LogRecord:
        cleaned: dict[str, Any] = {}
        for key, value in record.fields.items():
            if key.lower() in self.sensitive_fields:
                cleaned[key] = self.REDACTED
            elif isinstance(value, dict):
                cleaned[key] = {k: (self.REDACTED
                                    if k.lower() in self.sensitive_fields else v)
                                for k, v in value.items()}
            else:
                cleaned[key] = value
        record.fields = cleaned
        return record

    def is_clean(self, record: LogRecord) -> bool:
        """No sensitive key survives with real content."""
        for key, value in record.fields.items():
            if key.lower() in self.sensitive_fields and value != self.REDACTED:
                return False
        return True


class StructuredLogger:
    """§21.3. Emits correlated, redacted log records."""

    def __init__(self, formatter: RedactingFormatter | None = None):
        self.formatter = formatter or RedactingFormatter()
        self.records: list[LogRecord] = []

    def log(self, level: str, event: str, **fields) -> LogRecord:
        record = LogRecord(level=level, event=event, **{
            k: v for k, v in fields.items()
            if k in ("segment_id", "session_id", "correlation_id", "stage")
        }, fields={
            k: v for k, v in fields.items()
            if k not in ("segment_id", "session_id", "correlation_id", "stage")
        })
        record = self.formatter.redact(record)
        self.records.append(record)
        return record

    def info(self, event: str, **fields) -> LogRecord:
        return self.log("info", event, **fields)

    def error(self, event: str, **fields) -> LogRecord:
        return self.log("error", event, **fields)

    def correlated(self, event: str, segment_id: str, session_id: str,
                   correlation_id: str, stage: str, **fields) -> LogRecord:
        """§21.3: segment, session, correlation, and stage together."""
        return self.log("info", event, segment_id=segment_id,
                        session_id=session_id, correlation_id=correlation_id,
                        stage=stage, **fields)


# ── §21.4 Health: liveness vs readiness ──────────────────────────────


class Capability(Enum):
    BROADCAST = "broadcast"
    TRANSCRIPT = "transcript"
    INGESTION = "ingestion"
    COMPILATION = "compilation"
    TEXT_TO_SPEECH = "text_to_speech"
    WORLD_MODEL = "world_model"


@dataclass
class ReadinessReport:
    """§21.4. Names degraded capabilities rather than a single global flag."""
    ready: bool
    available: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "available": self.available,
            "degraded": self.degraded,
            "checked_at": self.checked_at.isoformat(),
        }


class HealthService:
    """§21.4. Liveness is the process; readiness is per-capability."""

    def __init__(self, metrics: MetricsRegistry | None = None):
        self.metrics = metrics or MetricsRegistry()
        self._process_alive = True

    def liveness(self) -> dict[str, Any]:
        """Whether the process functions sufficiently to keep running."""
        return {
            "alive": self._process_alive,
            "uptime_ok": self._process_alive,
        }

    def set_process_alive(self, alive: bool) -> None:
        self._process_alive = alive

    def readiness(self, capability: Capability) -> ReadinessReport:
        """Whether a specific capability currently works.

        §21.4: the system "may be able to serve transcripts and inspect
        historical stories while its TTS provider is unavailable."
        """
        subsystem_map = {
            Capability.BROADCAST: Subsystem.AUDIO,
            Capability.TRANSCRIPT: Subsystem.AUDIO,
            Capability.INGESTION: Subsystem.INGESTION,
            Capability.COMPILATION: Subsystem.COMPILATION,
            Capability.TEXT_TO_SPEECH: Subsystem.AUDIO,
            Capability.WORLD_MODEL: Subsystem.EDITORIAL,
        }
        state = self.metrics.health(subsystem_map[capability])
        available = ([] if state is HealthState.UNAVAILABLE
                     else [capability.value])
        degraded = ([capability.value] if state is HealthState.DEGRADED else [])
        return ReadinessReport(
            ready=state is not HealthState.UNAVAILABLE,
            available=available,
            degraded=degraded,
        )

    def full_readiness(self) -> ReadinessReport:
        """All capabilities at once, each individually reported."""
        available: list[str] = []
        degraded: list[str] = []
        for capability in Capability:
            report = self.readiness(capability)
            available.extend(report.available)
            degraded.extend(report.degraded)
        return ReadinessReport(
            ready=not degraded,
            available=available,
            degraded=degraded,
        )
