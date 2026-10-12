# Acceptance Criteria — Definition of Done

Source: spec §30 (Definition of Done), §22.10 (Acceptance Gates).

MindBroadcast is not complete because it produces a convincing demonstration of
an AI presenter reading a generated script. It is complete when it functions as
a **persistent broadcasting system whose information, decisions, and outputs
remain understandable and recoverable over time.**

## Acceptance gates (spec §22.10)

| Gate | Requirement | Phase | Status |
|---|---|---|---|
| 1 | Persistence and migrations are reliable. | 1 | **MET** — 253 tests, migration runner + recovery + legacy import verified |
| 2 | Ingestion and deduplication are idempotent. | 2 | NOT MET |
| 3 | Claims and evidence have traceable provenance. | 3 | PARTIAL — provenance is recorded and traceable at the persistence layer; extraction passes are Phase 3 |
| 4 | World snapshots preserve consistent historical state. | 4 | PARTIAL — snapshots capture full state with watermark/hash/manifest and an atomic boundary; temporal reasoning and replay are Phase 4 |
| 5 | Story selection is explainable and reproducible. | 4 | NOT MET |
| 6 | Generated scripts pass mandatory validation. | 5 | NOT MET |
| 7 | TTS and audio delivery recover from failures. | 5, 8 | NOT MET |
| 8 | Corrections propagate to affected claims and broadcasts. | 3, 5 | PARTIAL — corrections are first-class, linked, and preserved; propagation to broadcasts is Phase 5 |
| 9 | Listener interaction cannot bypass editorial controls. | 7 | NOT MET |
| 10 | Long-duration operation remains stable. | 8 | NOT MET |

Each gate has explicit tests and recorded results. **A feature is not complete
because a happy-path demonstration works once.**

## Final acceptance criteria

### Information integrity

| Criterion | Status |
|---|---|
| Every captured article has source provenance. | **MET** — `articles.source_id` FK, enforced |
| Claims remain distinguishable from evidence. | **MET** — separate tables; correction shortcut removed |
| Articles remain distinguishable from events and stories. | **MET** — three distinct tables |
| Corrections do not erase historical records. | **MET** — first-class table + append-only triggers |
| Entity and event relationships can be inspected. | PARTIAL — queryable; merge/review UI is Phase 7 |
| World snapshots have explicit temporal semantics. | **MET** — watermark, hash, manifest, atomic boundary |
| Processing retries do not create unintended duplicate records. | **MET** — idempotency keys + UNIQUE(content_hash) |
| The system can reconstruct the evidence available when a segment was generated. | NOT MET — segments are Phase 5 |

### Operational reliability

| Criterion | Status |
|---|---|
| The application survives process restarts. | **MET** — cross-instance on-disk persistence tested |
| Pending work can be recovered. | **MET** — outbox survives restart; verified |
| Database transactions have explicit failure semantics. | **MET** — centralized `_Transaction`, rollback tested |
| Queue growth and resource usage are bounded. | NOT MET — Phase 8 |
| Source failures do not terminate the entire system. | NOT MET — ingestion is Phase 2 |
| Model-provider failures degrade only affected capabilities. | NOT MET — Phase 5 |
| Backups can be restored. | NOT MET — Phase 8 |
| Logs and metrics make failures diagnosable. | PARTIAL — structured domain event log; metrics Phase 7 |

## Epistemic invariants as acceptance tests

All twenty invariants in EPISTEMIC_INVARIANTS.md must have passing tests before
the corresponding gate closes. The critical ones by gate:

| Gate | Invariants that must hold |
|---|---|
| 1 | I3 (correction ≠ evidence), I4 (history never rewritten), I5 (three time axes) — **all tested and passing** |
| 2 | I8 (volume ≠ corroboration), I9 (near-duplicates carry corrections) — I8/I9 tested; ingestion is Phase 2 |
| 3 | I1 (status ≠ truth score), I2 (statement-existence ≠ truth), I10 (not every conflict is a contradiction), I14 (a source list is not provenance) — I1/I2 tested; I10/I14 need the compiler |
| 4 | I8, I11 (uncertainty attaches where it occurs) — tested |
| 5 | I6 (personas cannot override evidence), I7 (multiple outputs ≠ corroboration), I12 (generated ≠ validated ≠ delivered), I13 (multi-mechanism validation) — Phase 5/6 |
| 7 | I15 (allegation ≠ assertion), I16 (config flags are not enforcement) — Phase 5/7 |

## Gate 1 evidence (recorded 2026-10-12)

Command: `python3 -m pytest -q`
Result: **253 passed, 0 failed** (baseline: 49 passed, 27 failed)

| File | Tests | Covers |
|---|---|---|
| test_models.py | 55 | Domain shape, enum sets, legacy status mapping |
| test_persistence.py | 90 | CRUD, FK enforcement, append-only triggers, snapshots, events, idempotency, outbox |
| test_epistemic.py | 32 | Invariants I1, I2, I3, I4, I5, I8, I9, I11 |
| test_snapshot.py | 18 | Snapshot creation, comparison, retrieval, artifacts |
| test_recovery.py | 24 | Transactions, crash recovery, fault injection, concurrency, integrity |
| test_migrations.py | 34 | Migration runner, lifecycle recovery, legacy import |

Additional verifications performed outside the suite: recovery refuses on an
injected orphaned foreign key (raises `RecoveryError`, enters `DEGRADED`);
all twelve append-only triggers abort their target statement; legacy import
passes recovery with zero integrity violations.

## Completion discipline

- Every gate closes with a **recorded test run**, not an assertion.
- A partial fix is not a fix: on a reported defect, find the root cause.
  A weaker band-aid that suppresses the visible symptom leaves the gate unmet.
- Known failures are not acceptable as a completed phase.
- The full suite must pass before a phase is declared done.
- Report actual numbers. Never fabricate or assume a result.
