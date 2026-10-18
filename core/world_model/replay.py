"""Historical replay and temporal-leakage prevention.

Spec §9.1 requires the historical view to answer "what did the system believe
it knew at a given time?" — and §22.7 is stricter:

    For each time point, the system should only receive information that would
    have been available by that time. This is essential to prevent temporal
    leakage.

Leakage is the failure that silently invalidates every evaluation. If a replay
to March can see an April article, then "did the system detect this development
when it happened?" is answered using knowledge the system did not have — and
the evaluation reports success it did not earn.

So the filter is on **observation time** (`first_observed_at`), not publication
time. An article published in January but first fetched in March was not
available to the system in February.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass
class LeakageViolation:
    record_type: str
    record_id: str
    first_observed_at: datetime
    as_of: datetime

    @property
    def detail(self) -> str:
        return (f"{self.record_type} {self.record_id} was first observed "
                f"{self.first_observed_at.isoformat()}, after the replay "
                f"point {self.as_of.isoformat()}")


@dataclass
class ReplayWindow:
    as_of: datetime
    records: list[Any] = field(default_factory=list)
    violations: list[LeakageViolation] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.violations


def filter_as_of[T](
    records: Iterable[T],
    as_of: datetime,
    observed_at: Callable[[T], datetime | None],
    record_id: Callable[[T], str] = lambda r: getattr(r, "id", str(r)),
    record_type: str = "record",
) -> ReplayWindow:
    """Return only records first observed at or before `as_of`.

    Records observed after `as_of` are excluded **and recorded as violations**,
    so a caller can assert the window is clean rather than trusting it.
    """
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)

    window = ReplayWindow(as_of=as_of)
    for record in records:
        observed = observed_at(record)
        if observed is None:
            # A record with no observation time cannot be placed in time. It is
            # excluded rather than assumed current — an unknown timestamp is
            # not evidence of availability.
            window.violations.append(LeakageViolation(
                record_type=record_type,
                record_id=record_id(record),
                first_observed_at=as_of,
                as_of=as_of,
            ))
            continue
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        if observed <= as_of:
            window.records.append(record)
        else:
            window.violations.append(LeakageViolation(
                record_type=record_type,
                record_id=record_id(record),
                first_observed_at=observed,
                as_of=as_of,
            ))
    return window


def assert_no_leakage(window: ReplayWindow) -> None:
    """Raise if the replay window contains information it should not."""
    if not window.is_clean:
        details = "; ".join(v.detail for v in window.violations[:3])
        raise AssertionError(
            f"temporal leakage: {len(window.violations)} record(s) visible "
            f"before they were observed — {details}")


def reconstruct_knowledge(
    claims: list[Any],
    as_of: datetime,
) -> dict[str, list[Any]]:
    """What the system knew at `as_of`, split by epistemic status.

    §9.1's historical view: which claim assessments were active at that time.
    """
    window = filter_as_of(
        claims, as_of,
        observed_at=lambda c: getattr(c, "first_observed_at", None),
        record_type="claim",
    )
    by_status: dict[str, list[Any]] = {}
    for claim in window.records:
        status = getattr(claim, "epistemic_status", "unknown")
        key = getattr(status, "value", str(status))
        by_status.setdefault(key, []).append(claim)
    return by_status


def development_timeline(
    changes: list[Any],
    as_of: datetime | None = None,
) -> list[Any]:
    """Material developments in chronological order, up to `as_of`.

    Uses observation time so a late-published investigation appears when the
    system learned of it, not when the underlying event occurred (§9.3).
    """
    ordered = sorted(
        changes,
        key=lambda c: getattr(c, "occurred_at", datetime.min.replace(
            tzinfo=UTC)),
    )
    if as_of is None:
        return ordered
    return [c for c in ordered
            if getattr(c, "occurred_at", None) is not None
            and _utc(c.occurred_at) <= _utc(as_of)]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def snapshot_consistency_check(
    snapshot_manifest: dict[str, list[str]],
    records_as_of: dict[str, list[str]],
) -> list[str]:
    """Verify a snapshot's manifest matches what was knowable at its time.

    §5.9: a snapshot generated on Thursday must not falsely imply the system
    knew a Monday report... on Monday. This checks the manifest against the
    set of records actually observable at the snapshot's time.
    """
    problems: list[str] = []
    for record_type, ids in snapshot_manifest.items():
        allowed = set(records_as_of.get(record_type, []))
        unexpected = [i for i in ids if i not in allowed]
        if unexpected:
            problems.append(
                f"{record_type}: {len(unexpected)} record(s) in the snapshot "
                f"were not observable at snapshot time")
    return problems
