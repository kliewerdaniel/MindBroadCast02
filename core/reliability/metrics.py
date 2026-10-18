"""Operational metrics.

Spec §21.1–21.2. "MindBroadcast should expose operational metrics for every
major subsystem."

§21.5 requires the dashboard show "real state from subsystem health checks, not
manually maintained decorative indicators." So the `MetricsRegistry` is the
single source the dashboard reads, and a subsystem's health is *derived* from
its metrics — never set by hand.

The six §21.2 subsystems, each with its named metrics.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class Subsystem(Enum):
    INGESTION = "ingestion"
    COMPILATION = "compilation"
    EDITORIAL = "editorial"
    LANGUAGE_MODEL = "language_model"
    AUDIO = "audio"
    SYSTEM = "system"


class HealthState(Enum):
    """§21.4. A subsystem's derived state, never a manual toggle."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    PROCESSING = "processing"
    UNAVAILABLE = "unavailable"


@dataclass
class Metric:
    name: str
    value: float
    subsystem: Subsystem
    kind: str = "counter"          # counter | gauge | rate
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class MetricsRegistry:
    """The single source of subsystem state (§21.5)."""

    def __init__(self):
        self._metrics: dict[str, Metric] = {}
        self._error_counts: dict[str, int] = {}

    def increment(self, subsystem: Subsystem, name: str,
                  amount: float = 1.0) -> None:
        key = f"{subsystem.value}.{name}"
        existing = self._metrics.get(key)
        if existing is None:
            self._metrics[key] = Metric(name, amount, subsystem, "counter")
        else:
            existing.value += amount
            existing.updated_at = datetime.now(UTC)

    def set_gauge(self, subsystem: Subsystem, name: str, value: float) -> None:
        key = f"{subsystem.value}.{name}"
        self._metrics[key] = Metric(name, value, subsystem, "gauge")

    def record_error(self, subsystem: Subsystem, name: str) -> None:
        """An error also increments the success/failure pair (§21.2)."""
        self.increment(subsystem, f"{name}_failures")
        self._error_counts[subsystem.value] = (
            self._error_counts.get(subsystem.value, 0) + 1)

    def get(self, subsystem: Subsystem, name: str) -> float:
        metric = self._metrics.get(f"{subsystem.value}.{name}")
        return metric.value if metric else 0.0

    def subsystem_metrics(self, subsystem: Subsystem) -> dict[str, float]:
        prefix = f"{subsystem.value}."
        return {k[len(prefix):]: m.value for k, m in self._metrics.items()
                if k.startswith(prefix)}

    def health(self, subsystem: Subsystem) -> HealthState:
        """Derived from metrics, never set by hand (§21.5)."""
        metrics = self.subsystem_metrics(subsystem)
        if not metrics:
            return HealthState.UNAVAILABLE
        failures = sum(v for k, v in metrics.items() if k.endswith("_failures"))
        successes = sum(v for k, v in metrics.items()
                        if k.endswith(("_succeeded", "_captured", "_discovered")))
        if failures == 0 and successes == 0:
            return HealthState.PROCESSING
        if successes == 0:
            return HealthState.UNAVAILABLE
        total = failures + successes
        failure_rate = failures / total
        if failure_rate >= 0.5:
            return HealthState.UNAVAILABLE
        if failure_rate >= 0.1:
            return HealthState.DEGRADED
        return HealthState.HEALTHY

    def all_health(self) -> dict[str, str]:
        """§21.5's pipeline view, from real subsystem checks."""
        return {s.value: self.health(s).value for s in Subsystem}

    def backlog(self, subsystem: Subsystem) -> float:
        """§21.2/§23.3: expose the backlog through observability."""
        return self.get(subsystem, "backlog")

    def snapshot(self) -> dict[str, Any]:
        return {
            "metrics": {k: m.value for k, m in self._metrics.items()},
            "health": self.all_health(),
        }
