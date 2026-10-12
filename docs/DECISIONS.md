# Decision Log

Architecture Decision Records for MindBroadcast. **Ratify before code** — per
ADR-gated engineering, a decision is recorded before the implementation it
governs.

Status values: `PROPOSED`, `RATIFIED`, `SUPERSEDED`, `DEPRECATED`.

---

## ADR-001: Local-first, single-process modular monolith

**Status:** RATIFIED
**Source:** spec §4.1, §29

**Context:** The spec describes ~20 logical modules. A distributed microservice
deployment would multiply operational failure modes for a system whose initial
deployment is one machine.

**Decision:** Single Python application process with multiple async workers and
bounded worker pools. Logical module boundaries are preserved as package/class
boundaries, not network boundaries.

**Rationale:** "Separating responsibilities does not require separating them
into network services." A modular monolith is preferable until there is a
demonstrated need for independent scaling, process isolation, or remote
execution.

**Consequences:** Module contracts must be explicit enough that a future split
is mechanical. Process separation may be introduced later for inference
isolation or reliability.

---

## ADR-002: Port-and-fix the existing models, or rebuild from scratch?

**Status:** PROPOSED — needs ratification before Phase 1 code

**Context:** The prior implementation (`kliewerdaniel/MindBroadcast`) has
~1,078 lines of production code: a SQLite persistence layer, 7 domain
dataclasses, and 49 passing tests. It also has 27 failing tests, four domain
gaps that violate the spec (no Source, no Article, Correction collapsed into
Evidence, snapshot is an aggregation), and only one UNIQUE constraint.

**Option A — Port and fix.** Copy `core/models.py`, `core/persistence.py`,
`core/snapshot.py` into this repo; apply the Phase 1 work stream (B1–B7) as
migrations and rewrites; carry forward the 49 passing tests.

**Option B — Rebuild from the documented contracts.** Write fresh models and
schema against DOMAIN_MODEL.md, with tests written first.

**Recommendation: Option A.** The persistence layer is 730 lines of working
SQLite plumbing (WAL, connection handling, append-only attempts) that is
genuinely reusable, and the 49 passing tests encode real behavior. The gaps are
structural (missing tables, wrong enum sets) and are addressed by migration, not
by discarding the code. Rebuilding would re-derive transaction handling that
already works while producing no additional correctness.

**Against Option A:** the schema gaps are deep enough (Article/Event split
requires moving data) that a careful migration is nearly as much work as a fresh
schema. If the port reveals the connection layer is unsound, fall back to
Option B for the persistence layer while keeping the tests as the specification.

**Decision rule:** read `core/persistence.py` in full. If transaction handling
and connection centralization are sound → Option A. If they are not → Option B.

---

## ADR-003: SQLite as system of record; relational graph interface

**Status:** RATIFIED
**Source:** spec §6.1, §27.1

**Decision:** SQLite with WAL, FK enforcement on every connection, explicit
transactions, bounded writers, versioned migrations. The knowledge graph is
expressed as relational tables with explicit, indexed association records. No
separate graph database.

**Rationale:** "The application needs graph semantics, but it does not
necessarily need a separate graph database." A dedicated graph index may be
introduced later as a **derived representation** — never a second source of
truth.

---

## ADR-004: Append-only provenance with transactional outbox

**Status:** RATIFIED
**Source:** spec §3.3, §6.2, §6.4

**Decision:** Historical records are never rewritten. Material changes create new
records linked by `supersedes_*` relationships. Important async work goes through
a transactional outbox (domain record + domain event + outbox message committed
atomically). Workers are idempotent; delivery is at-least-once.

**Rationale:** In-memory notification after commit loses work on crash;
notification before commit delivers work that never happened. The outbox makes
both impossible. Exactly-once delivery is not assumed.

---

## ADR-005: Failure-independent validation, not self-approval

**Status:** RATIFIED
**Source:** spec §15.4, §15.6

**Decision:** Script validation is a multi-stage pipeline (structural,
provenance, contradiction, attribution, epistemic consistency, temporal, safety,
delivery). The final step does not ask the model that wrote the script whether
its own output is correct. Deterministic checks are the backbone; a separate
verifier prompt is supplementary.

**Rationale:** A second LLM response is not a guarantee of truth. Errors are
caught by multiple failure-independent mechanisms.

---

## ADR-006: One presenter before multiple personas

**Status:** RATIFIED
**Source:** spec §25 Phase 5 before Phase 6, §29

**Decision:** Phase 5 delivers a single reliable presenter with full
validation, TTS, and audio delivery. Multi-persona debate arrives in Phase 6,
after the evidence model and validation pipeline are proven.

**Rationale:** "A sophisticated persona interface cannot compensate for a
database that loses corrections, and a fluent broadcast cannot compensate for a
claim that has no traceable evidence."

---

## ADR-007: YAML for source configuration

**Status:** RATIFIED (carried from prior repo Decision 5)
**Source:** prior `docs/DECISIONS.md`

**Decision:** Sources are configured in YAML (`feeds.yaml`), not exclusively via
database inserts. The DB holds source state (failure counts, last success); the
YAML holds source definitions.

**Consequence:** A config change and a DB migration are different operations.
Ratified config is versioned in git.

---

## ADR-008: Edge-TTS as default voice provider

**Status:** PROPOSED — needs ratification before Phase 5

**Context:** The prior repo (Decision 7) selected Edge-TTS. The spec §16.1
requires "a lightweight local TTS engine with predictable resource usage" and
§3.1 requires local-first defaults.

Edge-TTS is a network service. It satisfies "lightweight" and "predictable
resource usage" on the local machine but conflicts with the local-first default
and with "a network outage should not destroy local state."

**Recommendation:** keep Edge-TTS as the **default provider behind the TTS
interface** for Phase 5 (it is the fastest path to a working voice), with an
explicit `local` provider slot reserved. Ratify a genuinely local engine
(piper, or the user's Qwen3-TTS MLX setup) before Phase 8's reliability work,
since Gate 7 requires TTS recovery from failure and a network dependency
weakens that gate.

**Consequence if ratified as-is:** document that TTS is the one subsystem whose
default violates strict local-first, and that Gate 7 must account for provider
outage as a first-class failure mode.

---

## ADR-009: Test-count discipline

**Status:** RATIFIED
**Source:** spec §22, project history

**Decision:** A phase is complete when its gate passes on a real, recorded test
run — not when a target number of tests exists. Milestones are discovered
implicit semantic boundaries, not test-count targets.

**Rationale:** Chasing test counts produces tests that assert trivia. The
milestone is the invariant boundary discovered in the spec.

**Consequence:** every phase doc lists concrete exit criteria with real
commands, and completion reports actual output.

---

## ADR-010: Spec section references are normative in these docs

**Status:** RATIFIED

**Decision:** These docs cite the published spec as `§N`. Where a doc and the
spec disagree, the spec wins and the doc is corrected via a new ADR or a patch.

**Rationale:** The spec is the architectural contract; the docs are its
decomposition. Divergence must be resolved toward the contract, and the
resolution recorded.
