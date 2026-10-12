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
| 2 | Ingestion and deduplication are idempotent. | 2 | **MET** — 104 ingestion tests; all ten §7.7 criteria verified |
| 3 | Claims and evidence have traceable provenance. | 3 | **MET** — 71 compiler tests; claim→span→evidence chain verified |
| 4 | World snapshots preserve consistent historical state. | 4 | **MET** — replay filters on observation time; leakage reported, not silent |
| 5 | Story selection is explainable and reproducible. | 4 | **MET** — 54 world-model tests; all nine factors operationally defined |
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

## Gates 4 and 5 evidence (recorded 2026-10-12)

Command: `python3 -m pytest -q`
Result: **482 passed, 0 failed** (Phase 3 was 428; Phase 4 added 54)

| File | Tests | Covers |
|---|---|---|
| test_world_model.py | 54 | §9 change detection, §10 ranking, coverage, clustering, replay |

### Gate 4 — snapshots preserve consistent historical state

| Criterion | Result |
|---|---|
| A snapshot at T reconstructs exactly what was known at T | MET — manifest vs. knowable records: 0 violations |
| Valid, observation, transaction times distinct through replay | MET |
| Replay to T never includes information first observed after T | MET — future records excluded **and reported** |
| A change to one claim does not invalidate every story summary | MET — dependency-scoped invalidation |

### Gate 5 — story selection is explainable and reproducible

| Criterion | Result |
|---|---|
| All nine ranking factors operationally defined | MET — 8 computed factors + 2 penalties |
| Novelty measured against the world model, not article text | MET — 25 articles, no material change → novelty 0.0 |
| Temporal velocity uses material changes, not volume | MET — 50 duplicates → 0.0; 1 development → 0.85 |
| A late discovery is not ranked as stale | MET — velocity decays from observation time |
| Coverage saturation lifts on a material change | MET — saturation 1.0 → 0.8 |
| Every selection carries a recorded rationale with factor values | MET — structured decision record, weight-set id |
| Audience relevance bounded, cannot override importance | MET — popularity −0.35 vs consequential 3.45 |
| Same stored state reproduces the same ranking | MET — deterministic, weight-set id recorded |

### The §9.2 distinction, tested directly

Ten articles restating one known claim produce **zero** material changes. Ten
restatements cluster into **one** story. Article volume never reads as
development.

---

## Gates 6 and 7 evidence (recorded 2026-10-12)

Command: `python3 -m pytest -q`
Result: **633 passed, 0 failed** (Phase 4 was 482; Phase 5 added 151)

| File | Tests | Covers |
|---|---|---|
| test_broadcast.py | 151 | §13 director, §14 LLM orchestration, §15 validation, §16 audio |

### Gate 6 — approved script generation

| Criterion | Result |
|---|---|
| A generation request produces a persistent, versioned script | MET — request → `BroadcastScript` v1 with turns |
| Every substantive statement carries an epistemic function | MET — `EpistemicFunction` set and preserved |
| A factual claim cannot broadcast without resolving evidence **that supports it** | MET — a source list alone fails stage 2 |
| All 8 validation stages defined, each catches a failure class | MET — a broken script caught by 3 independent stages |
| `REJECTED` distinguishes unspeakable content from recoverable problems | MET — polished unsupported allegation → REJECTED |
| A malicious source cannot redirect the task | MET — injection pattern blocks generation |
| An unsupported allegation does not pass on polished prose | MET — tested with flowing prose |

### Gate 7 — continuous audio

| Criterion | Result |
|---|---|
| Approved segments synthesized in ordered chunks with sequence numbers | MET |
| Chunks bound to segment + synthesis attempt | MET |
| A cancelled/superseded generation's chunks never delivered | MET — `StaleChunk` raised |
| A slow client cannot consume unbounded memory | MET — byte budget raises `BufferFull` |
| Interrupted speech recorded and cannot replay | MET — audio dropped, history preserved |
| Generated ≠ validated ≠ submitted ≠ synthesized ≠ buffered ≠ delivered ≠ consumed | MET — 7 distinct `ContentState` values |

### §15.7 Post-broadcast correction

Original transcript preserved byte-identical; correction appended as a second
archive entry; original marked `superseded_by`, never deleted.

---

## Gate 9 evidence (recorded 2026-10-12)

Command: `python3 -m pytest -q`
Result: **712 passed, 0 failed** (Phase 5 was 633; Phase 6 added 79)

| File | Tests | Covers |
|---|---|---|
| test_personas.py | 79 | §12 persona architecture, dialogue, memory, consistency |

### Gate 9 — multi-persona coherent dialogue

| Criterion | Result |
|---|---|
| Multiple personas, distinct configured roles | MET — versioned configs |
| Initial analyses generated independently | MET — ordering enforced, not prompt-based |
| Each analysis records assumptions and evidence | MET — `PersonaAnalysis` fields |
| Disagreement identifies the precise proposition | MET — question or common words |
| Disagreement records shared facts | MET — both parties' accepted facts |
| No manufactured disagreement on clear evidence | MET — unanimous analyses suppressed |
| No forced consensus when unresolved | MET — `must_not_force_consensus` |
| Structured exchange addresses recorded disagreements | MET — turns trace to disagreement ids |
| Script validates against the shared packet | MET — `evidence_packet_id` set |
| Memory persistent, bounded, typed, provenance-carrying | MET — 5 types, active cap |
| Persona recalls a prior position and its weakening | MET — history retains withdrawn |
| Consistency distinguishes explainable change from drift | MET — 1.0 explained / 0.3 drift |
| Personality does not determine truth | MET — shared packet is the baseline |
| Unintentional imitation detectable | MET — correlated-error finding |

### §12.7 explainable-change distinction, tested directly

A position revision with a recorded CORRECTION entry scores `appropriate_revision`
1.0. The same revision with no recorded basis scores 0.3. Change is not
penalized; unexplained change is.

---

## Gate 8 evidence (recorded 2026-10-12)

Command: `python3 -m pytest -q`
Result: **802 passed, 0 failed** (Phase 6 was 712; Phase 7 added 90)

| File | Tests | Covers |
|---|---|---|
| test_audience_api.py | 90 | §17 audience, §18 UI/operator, §19 API/events |

### Gate 8 — operator observability and control

| Criterion | Result |
|---|---|
| Every §19.2 endpoint implemented, domain operations not raw tables | MET — 20 endpoints, own response types |
| Versioned; structured errors; no silent client breakage | MET — `api_version`, code/message/id/retryable |
| No raw DB access or unrestricted admin to unauthenticated client | MET — admin endpoints 401 |
| Listener message is a durable input event, non-blocking | MET — enqueues, emits event |
| A question is not automatically answered on air | MET — `response_segment_id` stays None |
| A listener cannot directly establish a claim | MET — no claims write path exists |
| Per-client rate limits and queue quotas bound influence | MET |
| Abuse classification catches spam, duplicates, coordination | MET — stem-matched markers |
| Feedback recorded as noisy, never truth/consensus | MET — confidence capped 0.5, `noisy=True` |
| SSE stream stable ids, replay, persisted | MET — replay after cursor, retention bound |
| SHOW ANALYSIS exposes all ten fields | MET — 10/10 |
| SHOW ANALYSIS presents no chain-of-thought | MET — no field + redaction |
| Transcript distinguishes generated from delivered | MET — unplayed yields empty public transcript |
| Interrupted/abandoned segment marked, not completed | MET |
| Operator controls cover §18.6; persistent actions confirmed + audited | MET |
| Dashboard answers the four §18.1 questions | MET — 4/4 |
| Status not communicated by color alone | MET — textual state enforced |

---

## Gate 3 evidence (recorded 2026-10-12)

Command: `python3 -m pytest -q`
Result: **428 passed, 0 failed** (Phase 2 was 357; Phase 3 added 71)

| File | Tests | Covers |
|---|---|---|
| test_ingestion.py | 104 | All ten §7.7 criteria, §7.6 security, §7.5 dedup, §7.3 scheduling |
| test_compiler.py | 71 | All eight §8 passes, attribution, CKU, provenance chain |

### The eight Gate 3 exit criteria, each verified

| # | Criterion | Result |
|---|---|---|
| 1 | A structured claim traces to the source passage it came from, by offset | MET — every claim's `span` reproduces its source substring |
| 2 | Extraction confidence is distinct from evidentiary support | MET — separate fields; new claims are UNASSESSED |
| 3 | Attribution survives extraction | MET — the three §8.2 sentences produce different types |
| 4 | `ATTRIBUTES` is never recorded as `SUPPORTS` | MET — structural assignment; zero violations |
| 5 | Conflicting numbers with different temporal scope are not contradictions | MET — Pass 7 compares qualifiers first |
| 6 | Every extracted object records extractor + version | MET — `component@version` on every record |
| 7 | Dependency tracking invalidates only affected derived objects | MET — `DerivedObjectsRegistered` events |
| 8 | CKU is an interface over existing objects, not a new table | MET — no `cku` table exists |

### The §8.2 attribution requirement, tested directly

Three sentences that mean different things, asserted to classify differently:

    A minister said that the policy had reduced inflation.  -> ATTRIBUTED
    The policy reduced inflation.                          -> OBSERVATIONAL
    An analyst argued that the policy may reduce inflation. -> ATTRIBUTED

Plus: an attributed claim's passage is `ATTRIBUTES`, and the test asserts the
`SUPPORTS` list is empty. Recording a transcript as supporting the proposition
it merely reports is invariant I2's most likely failure mode.

---

## Gate 2 evidence (recorded 2026-10-12)

Command: `python3 -m pytest -q`
Result: **357 passed, 0 failed** (Phase 1 baseline was 253; Phase 2 added 104)

| File | Tests | Covers |
|---|---|---|
| test_models.py | 55 | Domain shape, enum sets, legacy status mapping |
| test_persistence.py | 90 | CRUD, FK enforcement, append-only triggers, snapshots, events, idempotency, outbox |
| test_epistemic.py | 32 | Invariants I1, I2, I3, I4, I5, I8, I9, I11 |
| test_snapshot.py | 18 | Snapshot creation, comparison, retrieval, artifacts |
| test_recovery.py | 24 | Transactions, crash recovery, fault injection, concurrency, integrity |
| test_migrations.py | 34 | Migration runner, lifecycle recovery, legacy import |
| test_ingestion.py | 104 | All ten §7.7 criteria, §7.6 security, §7.5 dedup levels, §7.3 scheduling |

### The ten §7.7 criteria, each with a named test

| # | Criterion | Test |
|---|---|---|
| 1 | A configured feed can be fetched and parsed | `test_criterion_1_feed_is_fetched_and_parsed` |
| 2 | A malformed feed does not crash the application | `test_criterion_2_malformed_feed_does_not_crash` |
| 3 | Duplicate fetches create no duplicate articles | `test_criterion_3_duplicate_fetch_creates_nothing` |
| 4 | Article durably stored before processing scheduled | `test_criterion_4_article_stored_before_work_scheduled` |
| 5 | Source and observation timestamps preserved separately | `test_criterion_5_timestamps_preserved_separately` |
| 6 | Network failure does not corrupt captured information | `test_criterion_6_network_failure_preserves_data` |
| 7 | Retried work is idempotent | `test_criterion_7_retry_is_idempotent` |
| 8 | A restarted process recovers pending ingestion tasks | `test_criterion_8_restart_recovers_pending_work` |
| 9 | A revised article remains distinguishable | `test_criterion_9_revision_remains_distinguishable` |
| 10 | External content cannot execute application commands | `test_criterion_10_external_content_cannot_execute` |

Security coverage: 14 parametrized SSRF/scheme cases, redirect re-validation
(the property that a permitted first hop cannot reach a blocked host on a
later one), redirect-chain bound, response-size cap, active-content stripping,
injection flagging, and transient/permanent HTTP classification.

Additional verifications outside the suite: recovery refuses on an injected
orphaned FK; all twelve append-only triggers abort; legacy import passes
recovery with zero violations.

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
