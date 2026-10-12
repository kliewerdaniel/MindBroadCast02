# Phase 8 Plan — Reliability, Optimization, External Streaming

**Spec sections:** §21 (observability), §22.7–22.10 (long-duration/crash tests),
§23 (performance/resources), §27.4 (continuous operation), §30 (definition of done)
**Exit criterion:** Gate 10 (extended autonomous operation) — bounded resources,
recoverable state, measurable editorial quality
**Status:** PLANNED

---

## 1. What this phase builds

The final phase makes the system survive extended operation:

```
┌─ §21 Observability ───────── per-subsystem metrics, structured logs,
│                              liveness vs readiness (§21.4)
├─ §23 Priority scheduling ── 5 priority classes; broadcast delivery first
├─ §23.3 Backpressure ──────── bounded queues; coalesce; never fabricate
│                              to compensate for delay
├─ §22 Crash recovery ──────── kill -9 simulation; pending work recovers
├─ §23.4 Caching ───────────── explicit invalidation; audio reuse gated on
│                              script version + synthesis config
├─ §23.5 Resource routing ──── defer non-urgent work under contention
├─ §27.4 Continuous operation ─ silence is permitted; no fabricated novelty
└─ §28 Recursive improvement ─ propose/test/approve/deploy, bounded
```

## 2. Modules to build

### `core/reliability/metrics.py` — §21.2

Per-subsystem counters: ingestion, compilation, editorial, models, audio,
system health. Each metric is a named counter/gauge with a subsystem tag. The
`MetricsRegistry` is the single source the dashboard reads (§21.5: "real state
from subsystem health checks, not manually maintained decorative indicators").

### `core/reliability/logging.py` — §21.3 structured logs

Correlated entries carrying segment id, session id, correlation id, stage.
§21.3's privacy rule enforced: a `RedactingFormatter` strips listener message
content and source text from log payloads — diagnosis uses request id, model,
error category, not the raw content.

### `core/reliability/health.py` — §21.4 liveness vs readiness

Two distinct checks. Liveness = the process functions. Readiness = a specific
capability works. The readiness response names degraded capabilities, so "TTS
down but transcripts served" is representable — a single `healthy:false` would
obscure it, which §21.4 explicitly rejects.

### `core/reliability/scheduler.py` — §23.2–23.3

Five priority classes. Under load: defer P4 enrichment, cap concurrency,
coalesce redundant updates, bounded retries, expose backlog. §23.3's rule 7:
never generate duplicate segments to compensate for delayed work.

### `core/reliability/backpressure.py` — §23.3

Bounded queues with a defined policy per queue: preserve durable input, drop
low-priority, coalesce. `QueueFull` is a first-class outcome, not a crash.

### `core/reliability/cache.py` — §23.4

Explicit invalidation. A cached story summary invalidates on material claim
change / correction / obsolete snapshot. A cached audio artifact reuses only
when script version and synthesis config match.

### `core/reliability/recovery.py` — §22 crash recovery

`kill -9` simulation: the director recovers from persisted state (already built
in Phase 5). This module adds the *test harness* — a process that writes state,
is killed, and is verified to recover its pending work without duplication.

### `core/reliability/routing.py` — §23.5 resource-aware routing

Defer non-urgent work under contention; never silently lower quality. The
decision is based on task requirements, documented per §23.5's example.

### `core/reliability/continuous.py` — §27.4

The "permitted silence" rule: when nothing valuable can be added, the system
may remain silent or play an explicitly configured interstitial. `FILLER_ANALYSIS`
is an internal classification, never fabricated novelty.

### `core/reliability/improvement.py` — §28 recursive improvement

The observe→evaluate→propose→test→approve→deploy loop. Bounded to low-risk
reversible parameters. §28.2's hard rule: the system must not autonomously
weaken factual validation to broadcast more. §28.4: multiple quality
dimensions, never one optimizable score.

### `core/reliability/streaming.py` — §16.8 external streaming boundary

The Icecast/external-radio seam. The audio transport and metadata transport
stay logically separate (§16.8). An external server can be added without
changing the editorial engine.

## 3. What this phase deliberately does NOT build

| Deferred | Reason |
|---|---|
| A real Icecast server | The boundary and interface are built; running a live radio server is deployment, not architecture. |
| Real multi-day uptime | The long-duration *simulation* is built and tested; a literal week-long run is an operational task. |
| Semantic self-improvement | §28's loop is structural; the *evaluation* of a proposed ranking change against historical data is wired but the judgment of "overall improvement" stays human/measured, per §28.3. |

## 4. Test strategy

| Class | Count | Proves |
|---|---|---|
| `TestMetrics` | ~10 | Per-subsystem counters; dashboard reads real state |
| `TestStructuredLogging` | ~10 | Correlation fields; content redaction |
| `TestLivenessVsReadiness` | ~10 | Distinct; degraded capability named |
| `TestPriorityScheduling` | ~12 | 5 classes; broadcast first; defer P4 under load |
| `TestBackpressure` | ~12 | Bounded; coalesce; no duplicate segments |
| `TestCaching` | ~12 | Invalidation on claim change/correction/snapshot; audio reuse gated |
| `TestCrashRecovery` | ~12 | kill -9; pending work recovers; no duplication |
| `TestResourceRouting` | ~8 | Defer under contention; no silent quality drop |
| `TestContinuousOperation` | ~8 | Permitted silence; no fabricated novelty |
| `TestRecursiveImprovement` | ~14 | Bounded; cannot weaken validation; multi-dimension |
| `TestStreamingBoundary` | ~8 | Audio/metadata separation; pluggable server |
| `TestDefinitionOfDone` | ~8 | §30's five categories, each checkable |

Total: ~124 tests → ~926 suite.

## 5. Gate 10 criteria

1. Per-subsystem metrics are exposed for all six §21.2 subsystems
2. The dashboard reads real subsystem state, not decorative indicators (§21.5)
3. Logs are correlated (segment/session/correlation id) and redact sensitive content (§21.3)
4. Liveness and readiness are distinct; readiness names degraded capabilities (§21.4)
5. Work is scheduled by the five §23.2 priority classes; broadcast delivery is P0
6. Under overload, low-priority work is deferred and concurrency is capped (§23.3)
7. Redundant updates are coalesced; no duplicate segments compensate for delay (§23.3 rule 7)
8. Queues are bounded with a defined policy; overload preserves durable input (§23.3)
9. Cached artifacts have explicit invalidation rules (§23.4)
10. A cached audio artifact reuses only on matching script version + synthesis config (§23.4)
11. The system recovers pending work after a simulated kill -9 without duplication (§22)
12. Resource-aware routing defers non-urgent work without silently lowering quality (§23.5)
13. The system may remain silent when nothing valuable can be added (§27.4)
14. FILLER_ANALYSIS is an internal classification, never fabricated novelty (§27.4)
15. The improvement loop is bounded to reversible parameters (§28.2)
16. The system cannot autonomously weaken factual validation (§28.2)
17. Improvement is evaluated on multiple dimensions, not one optimizable score (§28.4)
18. Audio and metadata transports are logically separate and pluggable (§16.8)
19. The §30 definition-of-done categories are each represented by a check

## 6. Order of work

1. Metrics registry
2. Structured logging with redaction
3. Health: liveness vs readiness
4. Priority scheduler
5. Backpressure and bounded queues
6. Caching with invalidation
7. Crash-recovery harness
8. Resource-aware routing
9. Continuous operation (permitted silence)
10. Recursive improvement loop
11. Streaming boundary
12. Definition-of-done checks
13. Gate 10 verification
14. Final docs and commit

## 7. Known risks

**The improvement loop's judgment is bounded, not absent.** §28.3 says the
system evaluates a proposed change against historical data and a baseline. That
comparison is wired. But "is this an overall improvement" — trading breaking-news
detection against suppressed investigations — is exactly the multi-dimensional
judgment §28.4 warns against reducing to one score. The loop reports all
dimensions and refuses to auto-approve; the approval stays explicit. That is the
honest boundary.

**kill -9 recovery is simulated in-process.** The recovery logic is the Phase 5
`BroadcastDirector.recover`, tested against persisted state. A real SIGKILL of a
live process is an integration test, not a unit test; the harness simulates the
state-after-kill, not the signal.

**"Extended periods" is a simulation.** The long-duration test runs a compressed
workload with resource assertions, not a literal multi-day run. The ceilings and
the bound-checking are what's verified; wall-clock endurance is operational.
