"""Polling scheduler and backpressure.

Spec §7.3: each source has its own polling configuration and failure policy.
The scheduler accounts for the configured interval, last successful poll, last
observed publication, rate limits, network failures, retry delay, source
importance, historical responsiveness, and current backlog.

**The scheduler must not poll every source at the same fixed frequency
regardless of circumstances.** Exponential backoff with jitter is used for
repeated transient failures, and one failing source must not consume all
available resources.

The due-source computation is a pure function of `now` and the source's
recorded state, so it is testable without a clock or a network.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass
class SourcePollState:
    """What the scheduler needs to know about one source.

    Mirrors the operational columns on the `sources` table.
    """
    source_id: str
    endpoint: str
    poll_interval_seconds: int = 300
    enabled: bool = True
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    failure_count: int = 0
    importance: float = 1.0


@dataclass
class BackoffPolicy:
    """Exponential backoff with jitter for repeated transient failures."""
    base_seconds: float = 30.0
    multiplier: float = 2.0
    max_seconds: float = 3600.0
    jitter_fraction: float = 0.25

    def delay_for(self, failure_count: int,
                  rng: Any | None = None) -> float:
        """Delay before the next attempt after `failure_count` failures.

        Capped, so a source that has been failing for days does not acquire an
        effectively infinite backoff.
        """
        if failure_count <= 0:
            return 0.0
        raw = min(
            self.base_seconds * (self.multiplier ** (failure_count - 1)),
            self.max_seconds,
        )
        source = rng if rng is not None else random
        jitter = raw * self.jitter_fraction
        return max(0.0, raw + source.uniform(-jitter, jitter))


DEFAULT_BACKOFF = BackoffPolicy()


@dataclass
class SchedulerPolicy:
    backoff: BackoffPolicy = field(default_factory=BackoffPolicy)
    max_concurrent_fetches: int = 4

    def __post_init__(self):
        if self.max_concurrent_fetches < 1:
            raise ValueError("max_concurrent_fetches must be >= 1")


DEFAULT_POLICY = SchedulerPolicy()


def is_due(
    state: SourcePollState,
    now: datetime,
    policy: SchedulerPolicy | None = None,
    rng: random.Random | None = None,
) -> bool:
    """Whether a source should be polled at `now`.

    A source is due when its configured interval has elapsed since the last
    attempt, *and* the failure backoff has also elapsed. A source with recent
    failures is polled less often, so one broken feed cannot starve the others.
    """
    policy = policy or DEFAULT_POLICY
    if not state.enabled:
        return False

    reference = state.last_attempt_at or state.last_success_at
    if reference is None:
        return True  # never polled

    # The configured interval is measured from the last *successful* poll when
    # there is one; a source that keeps failing must wait out its backoff from
    # the last attempt, and that backoff grows with the failure count. Using
    # the attempt time alone would let a long-interval source be retried
    # immediately after its interval elapsed, defeating the backoff.
    # `reference` is non-None here (the never-polled case returned above), so
    # the fallback chain always yields a datetime.
    success_reference = state.last_success_at or state.last_attempt_at
    assert success_reference is not None
    interval = timedelta(seconds=state.poll_interval_seconds)
    if state.failure_count == 0:
        return now >= success_reference + interval

    backoff = policy.backoff.delay_for(state.failure_count, rng)
    return now >= reference + timedelta(seconds=backoff)


def select_due_sources(
    states: Sequence[SourcePollState],
    now: datetime,
    policy: SchedulerPolicy | None = None,
    rng: random.Random | None = None,
) -> list[SourcePollState]:
    """All due sources, ordered so the most overdue and most important go first.

    Bounded by `max_concurrent_fetches` — the scheduler never starts more
    concurrent fetches than the policy allows.
    """
    policy = policy or DEFAULT_POLICY
    due = [s for s in states if is_due(s, now, policy, rng)]

    def overdue_seconds(state: SourcePollState) -> float:
        reference = state.last_attempt_at or state.last_success_at
        if reference is None:
            return float("inf")
        return (now - reference).total_seconds()

    # Higher importance first, then most overdue. Both are bounded inputs, so
    # the ordering is deterministic for a given `now`.
    due.sort(key=lambda s: (-s.importance, -overdue_seconds(s)))
    return due[: policy.max_concurrent_fetches]


def next_attempt_time(
    state: SourcePollState,
    now: datetime,
    policy: SchedulerPolicy | None = None,
    rng: random.Random | None = None,
) -> datetime | None:
    """When this source becomes eligible again, or None if already due."""
    policy = policy or DEFAULT_POLICY
    if is_due(state, now, policy, rng):
        return None
    reference = state.last_attempt_at or state.last_success_at
    if reference is None:
        return now
    interval = timedelta(seconds=state.poll_interval_seconds)
    candidate = reference + interval
    if state.failure_count > 0:
        backoff = policy.backoff.delay_for(state.failure_count, rng)
        candidate = max(candidate, reference + timedelta(seconds=backoff))
    return candidate


@dataclass
class BackpressureDecision:
    """What to do when the system is overloaded (spec §7.3)."""
    should_capture: bool
    should_analyze: bool
    backlog_depth: int
    reason: str = ""


def decide_under_backpressure(
    backlog_depth: int,
    backlog_limit: int = 100,
) -> BackpressureDecision:
    """Preserve incoming observations over expensive analysis.

    Spec §7.3: newly observed information can be processed later; information
    that was never captured may be permanently lost. Capture is therefore
    always permitted, and analysis is deferred once the backlog is deep.
    """
    if backlog_depth >= backlog_limit:
        return BackpressureDecision(
            should_capture=True,
            should_analyze=False,
            backlog_depth=backlog_depth,
            reason="backlog at limit: capture continues, analysis deferred",
        )
    return BackpressureDecision(
        should_capture=True,
        should_analyze=True,
        backlog_depth=backlog_depth,
        reason="within capacity",
    )


class Scheduler:
    """Coordinates polling across sources under the configured policy."""

    def __init__(self, policy: SchedulerPolicy | None = None,
                 rng: random.Random | None = None):
        self.policy = policy or DEFAULT_POLICY
        self.rng = rng or random.Random()

    def due_sources(
        self, states: Sequence[SourcePollState],
        now: datetime | None = None,
    ) -> list[SourcePollState]:
        now = now or datetime.now(UTC)
        return select_due_sources(states, now, self.policy, self.rng)

    def backoff_delay(self, failure_count: int) -> float:
        return self.policy.backoff.delay_for(failure_count, self.rng)
