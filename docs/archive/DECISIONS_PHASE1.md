# ADR Addendum — Decisions Taken During Phase 1 Implementation

These decisions were made while building Phase 1. They are recorded separately
from the original ADR log because they emerged from implementation rather than
from the specification.

---

## ADR-011: Append-only is enforced by database triggers, not by convention

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** spec §3.3 requires append-only provenance. The pre-Phase-1 code
stated the rule in a module docstring and had a failing test
(`test_story_cannot_be_updated_directly`). A comment is not an invariant.

**Decision:** twelve `BEFORE UPDATE` / `BEFORE DELETE` triggers using
`RAISE(ABORT, ...)` across `domain_events`, `articles`, `claims`, `evidence`,
`corrections`, and `world_snapshots`.

**Rationale:** the invariant must hold even when a future write path forgets
the convention. Enforcement lives with the data, not in a caller's discipline.

**Scope:** append-only applies to *historical records*. Operational tables
(`sources`, `outbox_messages`, `idempotency_keys`) remain mutable — a source's
`failure_count` is operational state, and spec §3.3 explicitly permits mutable
operational state whose changes are reconstructable.

**Pitfall worth recording:** SQLite skips `BEFORE` triggers on empty tables.
A test that mutates an empty table will appear to pass while proving nothing.
Every append-only test populates its table first.

---

## ADR-012: Legacy timestamps that are missing fall back to the epoch

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** `core/legacy_import.py` must move legacy rows without corrupting
the valid/observation/transaction distinction (spec §5.9.1). Some legacy rows
carry no usable timestamp.

**Decision:** substitute `datetime(1970, 1, 1)` — an obviously-wrong sentinel —
rather than `datetime.now()`.

**Rationale:** substituting "now" fabricates an observation time, making a
decade-old record look freshly observed and silently poisoning freshness and
novelty calculations downstream. An epoch value is visibly wrong and flags the
row for review. **Never fabricate a timestamp to satisfy a non-nullable field.**

---

## ADR-013: The outbox has both a standalone and a transactional entry point

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** spec §6.4 requires the outbox row to commit atomically with the
domain write. The first implementation of `enqueue_outbox()` inserted without
committing, on the stated assumption that the caller's transaction would
commit it — but the only caller used it standalone, so the row was silently
lost on close. A fault-injection test caught this.

**Decision:** two methods.

- `enqueue_outbox(message_type, payload)` — commits. Safe standalone.
- `enqueue_outbox_in(conn, message_type, payload)` — inserts on a
  caller-owned transaction connection. The caller commits, making the domain
  write and the outbox row atomic.

**Rationale:** the atomic path must be explicit and opt-in. A method whose
correctness depends on an unstated caller contract will be misused.

---

## ADR-014: `SCHEMA_VERSION` and the migration ledger are different scales

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** the first recovery implementation compared the migration ledger
version against the global `SCHEMA_VERSION` constant and reported a spurious
schema mismatch on every clean start.

**Decision:** `SCHEMA_VERSION` numbers the *table definition*; the migration
ledger numbers the *migration sequence*. A single table revision may span
several migrations. Recovery checks that the ledger is non-empty, not that it
equals the constant.

**Consequence:** when a future table revision spans migrations 2–4, bump
`SCHEMA_VERSION` once and let the ledger advance by three.
