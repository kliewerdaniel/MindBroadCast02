# State Machines

Source: spec §2.3, §11.7, §13.2, §13.3, §13.5, §22.4.

The system contains several explicit state machines. **Invalid transitions must
be rejected**, not silently accepted. Tests enumerate each table below.

## 1. Operating mode (§2.3)

Separate from editorial mode. The system can be `LIVE` while performing
historical analysis rather than reporting breaking news.

```
STOPPED ──start──► LIVE ──stop/drain──► STOPPING ──drained──► STOPPED
                     │
                     ├──no work──► IDLE ──work available──► LIVE
                     │
                     ├──dependency failure──► DEGRADED ──recovered──► LIVE
                     │
                     ├──historical replay──► REPLAY ──done──► LIVE
                     │
                     ├──simulation request──► SIMULATION ──done──► LIVE
                     │
                     └──admin──► MAINTENANCE ──complete──► LIVE
```

| Mode | Meaning |
|---|---|
| `LIVE` | Continuously selects and produces broadcast segments. |
| `IDLE` | Operational, no current speech to deliver. |
| `DEGRADED` | Continues using available subsystems while one or more dependencies are unavailable. |
| `REPLAY` | Replays previously generated audio or a historical broadcast session. |
| `SIMULATION` | Runs the editorial pipeline against controlled/historical data without publishing live audio. |
| `MAINTENANCE` | Schema migrations, maintenance, administrative operations. |
| `STOPPING` | Stops accepting new work; drains or cancels in-flight operations per shutdown policy. |
| `STOPPED` | All managed processing and delivery loops terminated. |

Rules:

- `DEGRADED` is a **mode, not an error**. Readiness reporting must identify
  which capabilities are degraded (spec §21.4).
- `SIMULATION` never publishes live audio.
- `STOPPING` drains gracefully; in-flight audio finishes or is explicitly
  abandoned with a record.
- `MAINTENANCE` blocks ordinary editorial work.

## 2. Editorial planner (§11.7)

```
IDLE
  ↓
EVALUATING
  ↓
PLAN_PROPOSED
  ↓
PLAN_VALIDATED
  ↓
SEGMENT_QUEUED
  ↓
SEGMENT_ACTIVE
  ↓
SEGMENT_COMPLETED
  ↓
REASSESSING
  └──────────► EVALUATING
```

Additional transitions required for cancellation, invalidation, failure,
interruption, and recovery.

Rules:

- **A proposed segment is not equivalent to an active segment.**
- **A generated script is not equivalent to a delivered segment.**
- A plan is a **proposal** that may be revised when material developments occur.
- Once a segment has begun generating audio, its already published portions are
  never retroactively rewritten.

## 3. Segment lifecycle (§13.3)

```
PROPOSED
    ↓
PLANNED
    ↓
GENERATING
    ↓
GENERATED
    ↓
VALIDATING
    ↓
APPROVED
    ↓
SYNTHESIZING
    ↓
READY
    ↓
QUEUED
    ↓
PLAYING
    ↓
COMPLETED
```

Failure and cancellation transitions required at each appropriate stage.

### Key semantic rules

| Situation | Correct behavior |
|---|---|
| TTS request fails | Segment stays `APPROVED`; media subsystem retries synthesis. Evidence and script are not invalidated. |
| Script validation fails | Return to generation or mark `REJECTED`. **Must not enter the playback queue.** |
| Validation passes with qualifications | `APPROVED_WITH_QUALIFICATIONS`; repair by adding attribution, narrowing the claim, or explicitly describing uncertainty. |
| Segment canceled or superseded | Its obsolete-generation audio chunks must never be delivered as if current. |

### Explicitly invalid transitions (§22.4)

```
APPROVED → SYNTHESIZING    VALID
APPROVED → PLAYING         INVALID   (must pass through SYNTHESIZING/READY/QUEUED)
REJECTED → PLAYING         INVALID
COMPLETED → COMPLETED      IDEMPOTENT
CANCELED → PLAYING         INVALID
```

Invariants enforced by property tests:

- **No segment becomes playable without passing validation.**
- **No canceled synthesis attempt publishes stale audio chunks into the active session.**

## 4. Interruption actions (§11.6)

Three actions, evaluated against materiality, urgency, evidence quality,
public-safety relevance, interruptibility of the current segment, verification
sufficiency, and whether a brief interruption beats a full topic change.

```
CONTINUE     preserve the current segment
QUEUE_NEXT   give the development priority after the current segment
INTERRUPT    suspend ordinary programming for an urgent, sufficiently supported update
```

Rules:

- Default to **segment-boundary interruption**: the current short turn finishes
  before the next urgent segment begins.
- Genuinely urgent situations may interrupt mid-segment, but the system must
  record any abandoned speech and prevent abandoned audio from accidentally
  playing after the interruption.
- **An interruption must not discard the current segment's history.** Record
  where it stopped, whether it completed its intended point, and whether the
  subject should resume later.

## 5. Claim status transitions (§5.6.2)

```
UNASSESSED ──evidence linked──► SUPPORTED ──independent distinct evidence──► CORROBORATED
     │                              │
     │                              ├──conflicting evidence──► DISPUTED
     │                              │                              │
     │                              │                              ├──material conflict──► CONTRADICTED
     │                              │                              └──reconciled──► SUPPORTED
     │                              │
     │                              ├──correction──► SUPERSEDED
     │                              └──retracted──► WITHDRAWN
     │
     └──evidence unavailable──► UNRESOLVED ──evidence arrives──► SUPPORTED
```

Every transition requires an explicit transition rule and a durable event. A
transition is never implicit.

## 6. Event status transitions (§5.5)

```
EMERGING ──► ACTIVE ──► DEVELOPING ──► DORMANT ──► CONCLUDED
                              │
                              ├──sources disagree──► DISPUTED
                              └──merged with another──► MERGED

Any state ──superseded──► SUPERSEDED
```

These statuses describe the **system's representation**, not an objective
universal state. Disagreement between sources is represented, not silently resolved.

## 7. Correction workflow (§15.7)

```
Correction detected
    ↓
Identify the affected claim
    ↓
Identify every broadcast segment that referenced it
    ↓
Determine whether the new information materially changes the statement
    ↓
Record the reason for the revised assessment
    ↓
Generate a correction script if required
    ↓
Validate the correction
    ↓
Give the correction appropriate editorial priority
    ↓
Link the correction to the original segment
    ↓
Preserve the original transcript and audio history
    ↓
Mark affected summaries and analytical artifacts for reassessment
```

A correction never replaces the original transcript with revised wording.

## 8. Listener message processing (§17.3)

```
Received → Validated → Rate-limited → Sanitized → Persisted → Classified
    → Stories/questions identified → Eligibility determined
    → Editorial queue → (selected?) → Response segment generated
    → Message↔segment relationship recorded
```

A listener question is **not automatically answered on air**. The system may
decline, defer, summarize, or combine requests per editorial policy. A listener
request may create a research task; it must not directly establish a claim in
the world model.

## State-machine testing (§22.4)

For every machine above:

1. Enumerate the valid transition table in code (single source of truth).
2. Assert every invalid transition is **rejected**.
3. Property-based tests generate event sequences and verify invariants hold:
   no segment playable without validation; no stale audio from canceled
   synthesis; no claim status change without a durable event.
