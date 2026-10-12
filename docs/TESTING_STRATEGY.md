# Testing Strategy

Source: spec §22.

MindBroadcast needs a strategy that distinguishes **deterministic correctness**
from **probabilistic quality**. A test verifying a DB record exists is
fundamentally different from an evaluation judging whether a generated political
analysis is balanced, useful, and factually supported. Both matter; they require
different methods.

## Testing layers (§22.1)

| # | Layer | Nature |
|---|---|---|
| 1 | Unit tests | Deterministic |
| 2 | Database and migration tests | Deterministic |
| 3 | Integration tests | Deterministic |
| 4 | Contract tests | Deterministic |
| 5 | State-machine tests | Deterministic |
| 6 | Fault-injection tests | Deterministic |
| 7 | End-to-end tests | Deterministic |
| 8 | Language-model evaluation | Probabilistic |
| 9 | Editorial quality evaluation | Probabilistic |
| 10 | Long-duration operational tests | Deterministic + probabilistic |
| 11 | Security tests | Deterministic |
| 12 | Recovery and data-integrity tests | Deterministic |

**The first seven categories form the deterministic engineering foundation.**
The probabilistic evaluation layers build on top of that foundation rather than
compensating for infrastructure that is not yet reliable.

## Layer 1: Unit tests (§22.2)

Cover: URL canonicalization, content hashing, duplicate detection,
entity-resolution rules, claim normalization, evidence relationships,
story-ranking calculations, coverage saturation, snapshot boundaries,
state-machine transitions, idempotency keys, script validation, duration
estimation, audio chunk ordering, correction propagation, retention decisions.

**Use pure functions wherever practical.** A story-ranking test must be able to
verify that a material correction increases a story's eligibility **without a
live language model**.

## Layer 2: Database tests (§22.3)

Verify: foreign-key enforcement, uniqueness constraints, transaction rollback,
concurrent reads and writes, migration correctness, cross-process persistence,
snapshot consistency, recovery after interrupted writes, append-only event
behavior, source and article relationships, correction history preservation,
referential integrity of provenance records.

**Use realistic database configurations.** An in-memory SQLite database is
useful for fast unit tests but **cannot substitute** for testing the
application's actual on-disk persistence and process-restart behavior.

## Layer 3: Integration tests

Exercise real subsystem composition with stub providers where a live model is
unnecessary: ingestion → article capture → knowledge compilation → claim/evidence
persistence, verified through the outbox and event log.

## Layer 4: Contract tests

- Provider interface conformance (LLM, TTS, source adapters): each stub
  implements the full contract; a provider that claims an unsupported capability
  fails its contract test.
- API schemas: request validation, structured error shape, versioning.

## Layer 5: State-machine tests (§22.4)

For every machine in STATE_MACHINES.md:

1. Enumerate the valid transition table in code as the single source of truth.
2. Assert every invalid transition is rejected.
3. Property-based tests generate event sequences and verify invariants:
   - No segment becomes playable without passing validation.
   - No canceled synthesis attempt publishes stale audio chunks.
   - No claim status change occurs without a durable event.

## Layer 6: Fault injection (§22.5)

| Failure | Expected behavior (specified before the test) |
|---|---|
| RSS endpoint timeout | Retry with backoff + jitter; source failure counter increments; no data loss. |
| Malformed feed response | Article rejected; application keeps running. |
| Database busy error | Bounded busy timeout; retry; no transaction held across I/O. |
| Process termination during a transaction | On restart: no partial records appear complete; recovery requeues safely. |
| Language-model timeout | Deadline enforced; retry policy applied; segment not committed as complete. |
| Invalid model output | Schema validation fails; correction-prompt retry; bounded attempts. |
| TTS failure mid-segment | Segment stays APPROVED; synthesis retried; no DB record claims a playable file that doesn't exist. |
| Browser disconnect during playback | Reconnect at segment boundary or live position; no duplicate delivery confusion. |
| Outbox dispatcher crash | Pending messages identified on restart and republished; idempotent workers absorb duplicates. |
| Duplicate event delivery | No duplicate active records created. |
| Source correction after a segment aired | Correction workflow identifies the affected segment; original transcript preserved. |
| Missing audio artifact | Detected by consistency check; never reported as playable. |
| Worker restart with an outstanding lease | Lease expired and requeued where safe. |

**For each failure, the expected recovery behavior is specified before the test
is written.**

## Layer 7: End-to-end tests

Full pipeline against fixture feeds and stub providers: article in → claim and
evidence persisted → story updated → segment planned → script generated →
validated → synthesized → delivered → transcript recorded → event log complete.
Runs with no live model.

## Layer 8: Knowledge quality evaluation (§22.6)

A representative corpus containing articles with **known relationships**:

- Duplicate articles.
- Syndicated articles.
- Corrections.
- Conflicting reports.
- Multiple events in one article.
- Several articles describing one event.
- Ambiguous entities.
- Attributed allegations.
- Quantitative claims with different denominators.
- Claims that change over time.
- Deliberately misleading content.
- Prompt-injection attempts.

Measure and report **separately**: extraction precision and recall,
entity-resolution accuracy, event-association accuracy, evidence-linking
accuracy, correction handling.

> **A system that extracts many claims but frequently misattributes them is not
> successful because its overall extraction rate is high.**

## Layer 9: Editorial evaluation (§22.7)

Evaluate using historical data and a controlled replay environment. **For each
time point, the system receives only information that would have been available
by that time** — this prevents temporal leakage.

Questions the evaluation asks:

- Did the system detect material developments?
- Did it avoid repeatedly selecting duplicate stories?
- Did it prioritize important events appropriately?
- Did it distinguish article volume from genuine corroboration?
- Did it recognize corrections?
- Did it preserve coverage of important but less popular subjects?
- Did it return to unresolved questions when appropriate?
- Did it avoid inventing connections between unrelated events?
- Did its selected segments add information beyond previous coverage?

Human evaluation supplements quantitative metrics where importance, clarity, and
usefulness are involved.

## Layer 9b: Broadcast quality evaluation (§22.8)

Evaluate on: factual grounding, attribution accuracy, clarity, naturalness,
analytical depth, appropriate uncertainty, repetition, persona differentiation,
segment coherence, transition quality, duration accuracy, correction quality,
listener comprehension.

Also measure the **difference between the script and the actual synthesized
output** where TTS normalization or pronunciation handling changes what is
delivered.

## Layer 10: Long-duration testing (§22.9)

Extended simulations with:

- Bursts of incoming articles.
- Long periods of low news volume.
- Repeated source failures.
- Model provider interruptions.
- Growing knowledge bases.
- Repeated corrections.
- Audience-message bursts.
- Long-running audio sessions.
- Process restarts during active programming.

Goal: determine whether queues grow without bound, storage expands unexpectedly,
repeated work accumulates, and editorial quality degrades over time.

## Layer 11: Security tests (§20, §7.6, §14.5)

- Prompt injection via source documents — article content instructing the model
  to change policy must not alter policy.
- Prompt injection via listener messages.
- SSRF: user-configured or redirected URLs reaching loopback/private/metadata
  endpoints must be blocked.
- Malformed and malicious network responses.
- Unauthorized administrative access (loopback default; admin endpoints never
  public by default).
- Secret leakage: no credentials in prompts, logs, transcript exports, debug
  bundles, API responses, or client-facing errors.
- Listener spam and coordinated topic manipulation.
- Least privilege: source adapters cannot modify personas; audio cannot alter
  claims; listener input cannot modify trust profiles.

## Layer 12: Recovery and data-integrity tests

- Restart with pending outbox messages.
- Restart with an in-flight broadcast session.
- Restart mid-transaction (simulated kill).
- Artifact reconcile: orphaned files and missing references.
- Correction-history preservation across restarts.
- Append-only enforcement: attempts to mutate historical records are rejected.

## Acceptance gates (§22.10)

| Gate | Requirement |
|---|---|
| 1 | Persistence and migrations are reliable. |
| 2 | Ingestion and deduplication are idempotent. |
| 3 | Claims and evidence have traceable provenance. |
| 4 | World snapshots preserve consistent historical state. |
| 5 | Story selection is explainable and reproducible. |
| 6 | Generated scripts pass mandatory validation. |
| 7 | TTS and audio delivery recover from failures. |
| 8 | Corrections propagate to affected claims and broadcasts. |
| 9 | Listener interaction cannot bypass editorial controls. |
| 10 | Long-duration operation remains stable. |

Each gate has explicit tests and recorded results. See ACCEPTANCE_CRITERIA.md.

## Invariant tests

Every invariant in EPISTEMIC_INVARIANTS.md gets a test that:

1. Constructs the violating condition deliberately.
2. Asserts the system refuses, flags, or preserves the distinction.
3. Fails loudly rather than silently accepting.

## Suite health rules

- **The full suite must pass.** Known failures are not acceptable as a
  completed phase — "49 passed, 25 failed" is not done.
- A test that passes in isolation but fails in the full suite indicates a
  fixture isolation problem, not a logic bug. Fix the isolation; do not delete
  or skip the test.
- Async tests need explicit asyncio marking or explicit event-loop scoping.
  See PHASE_0_AUDIT.md — the current 27 failures are dominated by an async
  fixture bug of exactly this kind.
- Re-run the entire suite after each fix, not just the affected test.
- Never fabricate or assume a test result. Report actual counts.
