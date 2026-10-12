# Roadmap

Source: spec §25 (phases), §26 (remediation priorities), §22.10 (gates).

The implementation proceeds through explicit milestones, each producing a
**working and testable system**. The first priority is not feature count — it is
establishing a reliable foundation on which continuous broadcasting can be built.

## Dependency order

```
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4
                                   (audit)   (domain)  (ingest)  (compile)  (world)
                                                          │
                                                          ▼
                                                    Phase 5 ──► Phase 6 ──► Phase 7 ──► Phase 8 ──► Phase 9
                                                  (1-persona    (personas    (audience    (reliability (recursive
                                                   broadcast)    + debate)     + UI)       + stream)    improvement)
```

Phases 0–4 are strictly ordered. Phase 5 requires Phase 4. Phase 6 requires
Phase 5. Phase 7 requires 5 and 6. Phase 8 requires 7. Phase 9 requires 8.

## Phase overview

| Phase | Objective | Gate |
|---|---|---|
| 0 | Repository and implementation audit | Baseline reproducible |
| 1 | Persistence and domain integrity | Gate 1 |
| 2 | Ingestion and source normalization | Gate 2 |
| 3 | Knowledge compilation | Gate 3 |
| 4 | World model and story intelligence | Gate 4, Gate 5 |
| 5 | Single-persona broadcasting | Gate 6 |
| 6 | Multiple personas and analytical dialogue | — |
| 7 | Live audience and operator experience | Gate 9 |
| 8 | Reliability, optimization, external streaming | Gate 7, Gate 10 |
| 9 | Recursive improvement layer | — |

---

## Phase 0: Repository and implementation audit

**Objective:** Establish the actual state of the project before further
architectural expansion.

Tasks:

- Inventory the repository and its modules.
- Read the existing design documents.
- Inspect the current database schema.
- Inspect the domain models.
- Run the full test suite.
- Reproduce reported failures.
- Identify incomplete migrations.
- Compare documented features with implemented behavior.
- Identify duplicated responsibilities.
- Establish the current startup and shutdown lifecycle.
- Record known defects and missing invariants.

Deliverables:

- Repository architecture report. ✓ `docs/PHASE_0_AUDIT.md`
- Current-state component map. ✓ `docs/IMPLEMENTATION_MATRIX.md`
- Test and defect report. ✓ `docs/PHASE_0_AUDIT.md` §Baseline
- Implementation-to-specification matrix. ✓ `docs/IMPLEMENTATION_MATRIX.md`
- Prioritized remediation plan. ✓ `docs/PHASE_1_PLAN.md`

**Exit criterion:** The current architecture and its known defects are
documented, and the baseline test results are reproducible.

**Status: COMPLETE.** Baseline reproduced: 49 passed, 27 failed.

---

## Phase 1: Persistence and domain integrity

**Objective:** Make the existing information model trustworthy.

Tasks:

- Ensure `Source` is persistently modeled.
- Distinguish `Article`, `Event`, and `Story`.
- Keep `Correction` distinct from `Evidence`.
- Establish consistent domain identifiers.
- Enforce foreign keys.
- Add or repair database migrations.
- Verify append-only provenance.
- Define snapshot semantics.
- Verify on-disk and cross-process persistence.
- Add transaction and recovery tests.
- **Fix existing test failures before expanding the feature set.**

Deliverables:

- Stable domain schema.
- Migration suite.
- Repository interfaces.
- Persistence tests.
- Documented transaction boundaries.
- Explicit recovery behavior.

**Exit criterion (Gate 1):** The system can reliably persist and retrieve its
core domain records without violating the defined relationships.

Detailed plan: `docs/PHASE_1_PLAN.md`.

---

## Phase 2: Ingestion and source normalization

**Objective:** Build a durable pipeline from external sources to normalized articles.

Tasks:

- Implement source adapters (RSS/Atom first).
- Add polling and retry policies (backoff with jitter, per-source config).
- Normalize URLs and timestamps (conservative, source-aware, reversible).
- Preserve source versions.
- Implement exact and near-duplicate detection.
- Add a transactional outbox.
- Build ingestion observability.
- Test malformed sources and network failures.

Deliverables: working ingestion service, source registry, normalized article
store, deduplication engine, durable processing queue.

**Exit criterion (Gate 2):** New source material can be captured, deduplicated,
and scheduled for processing **without loss of already committed information.**

**Status: COMPLETE — Gate 2 MET.** 104 ingestion tests; all ten §7.7 criteria
verified. Delivered: `core/ingestion/` with security (§7.6), canonicalization
(§7.4), deduplication (§7.5), scheduling (§7.3), adapters (§7.2), fetcher, and
the §7.1 pipeline. Plan: `docs/PHASE_2_PLAN.md`.

---

## Phase 3: Knowledge compilation

**Objective:** Transform articles into structured claims and evidence.

Tasks:

- Implement document normalization (Pass 1: structural).
- Add entity extraction (Pass 2).
- Add claim extraction (Pass 3).
- Add evidence linking (Pass 4).
- Add entity resolution (Pass 5).
- Add event resolution (Pass 6).
- Add contradiction and corroboration analysis (Pass 7).
- Add claim status tracking.
- Implement correction handling.
- Knowledge integration with transactional semantics (Pass 8).
- Introduce versioned extraction artifacts.
- Add knowledge-quality evaluations.

Deliverables: Knowledge Compiler, claim and evidence registry, entity and event
associations, provenance graph, compiler evaluation corpus.

**Exit criterion (Gate 3):** The system can trace a structured claim to the
source material from which it was extracted and **distinguish extraction
confidence from evidentiary support.**

**Status: COMPLETE — Gate 3 MET.** 71 compiler tests; all eight exit criteria
verified. Delivered: `core/compiler/` with the eight §8 passes. Default
extractors are deterministic (spaCy NER + rule-based classification) so every
pass is reproducible from its version string alone (ADR-020). CKU is a
read-only view with no table of its own (ADR-024).
Plan: `docs/PHASE_3_PLAN.md`. Decisions: `docs/DECISIONS_PHASE3.md`.

---

## Phase 4: World model and story intelligence

**Objective:** Maintain an evolving view of developments rather than a collection
of unrelated articles.

Tasks:

- Implement story entities.
- Associate stories with events and claims.
- Track material changes.
- Generate world snapshots.
- Implement temporal reasoning (three time axes).
- Add novelty detection.
- Add coverage history (coverage debt + saturation).
- Track unresolved questions.
- Build the initial editorial-ranking function.

Deliverables: world model, snapshot service, story engine, explainable ranking
records, historical replay capability.

**Exit criterion (Gates 4, 5):** The system can identify new developments,
connect them to ongoing stories, and reconstruct what information was available
at a given point in time — with story selection explainable and reproducible.

**Status: COMPLETE — Gates 4 and 5 MET.** 54 world-model tests; all exit
criteria verified. Delivered: `core/world_model/` with material change
detection (§9.2), the nine ranking factors operationally defined (§10),
coverage debt and non-permanent saturation (§10.8–9), deterministic story
clustering (§5.5.1), and temporal-leakage-safe replay (§22.7).
Plan: `docs/PHASE_4_PLAN.md`. Decisions: `docs/DECISIONS_PHASE4.md`.

---

## Phase 5: Single-persona broadcasting

**Objective:** Produce validated spoken segments from the persistent world model.

Tasks:

- Define the generation-request contract.
- Implement model-provider abstraction.
- Build context assembly.
- Implement structured script generation.
- Add claim-to-evidence validation (all 8 stages).
- Add a local TTS provider.
- Build the audio buffer.
- Implement the segment state machine.
- Record broadcast history.
- Add the live transcript interface.

Deliverables: one functioning presenter, continuous segment scheduling, local
speech synthesis, live transcript, evidence-linked script artifacts.

**Exit criterion (Gate 6):** The system can repeatedly generate, validate,
synthesize, and deliver short segments **without losing state when an individual
task fails.**

---

## Phase 6: Multiple personas and analytical dialogue

**Objective:** Support persistent analytical perspectives and structured disagreement.

Tasks:

- Implement versioned persona configurations.
- Add persona memory (episodic, semantic, preference, open-question, correction).
- Generate independent initial analyses.
- Identify substantive disagreements.
- Compose multi-persona exchanges.
- Add persona-specific voice configuration.
- Evaluate factual consistency and persona differentiation.

Deliverables: multiple configurable personas, persistent persona memory,
structured disagreement records, multi-voice broadcasting.

**Exit criterion:** Multiple personas can participate coherently while sharing a
common evidence model and preserving distinct analytical roles.

---

## Phase 7: Live audience and operator experience

**Objective:** Make the system interactive and operationally inspectable.

Tasks:

- Implement the main dashboard.
- Build SHOW ANALYSIS.
- Add the world explorer.
- Add listener messages.
- Implement rate limits.
- Add audience preference controls.
- Add system health visualization (real state, not decorative lights).
- Implement source and persona administration.
- Add transcript export and correction inspection.

Deliverables: browser-based control console, interactive listener channel,
evidence inspection tools, administrative controls, operational dashboard.

**Exit criterion (Gate 9):** An operator can understand the system's current
state, inspect why it selected a segment, and safely influence future
programming — and **listener interaction cannot bypass editorial controls.**

---

## Phase 8: Reliability, optimization, and external streaming

**Objective:** Prepare the system for extended autonomous operation.

Tasks:

- Conduct long-duration simulations.
- Test crash recovery.
- Optimize inference concurrency.
- Introduce resource-aware scheduling.
- Add audio reconnection support.
- Evaluate optional Icecast integration.
- Improve source-diversity monitoring.
- Test backup and restoration.
- Conduct security and privacy reviews.
- Establish operational service-level targets.

Deliverables: recovery procedures, resource management policies, long-duration
test reports, optional external streaming, operational documentation.

**Exit criterion (Gates 7, 10):** The system can operate for extended periods
while maintaining bounded resource usage, recoverable state, and measurable
editorial quality.

---

## Phase 9: Recursive improvement layer

**Objective:** A controlled improvement loop over the broadcaster's own
artifacts — evaluation and proposal first, controlled deployment afterward.
See EDITORIAL_MODEL.md §"The Recursive Improvement Layer."

Initially limited to low-risk, reversible parameters. Higher-impact changes
require stronger evidence and explicit approval. **The system must not
autonomously weaken factual validation.**

**Exit criterion:** The system can identify recurring deficiencies, propose
bounded improvements, demonstrate whether they work against a baseline, and
version/reverse approved changes.

---

## Remediation priorities (spec §26.1)

The ordering is deliberate. The most visually impressive feature is not the most
important engineering task. A sophisticated persona interface cannot compensate
for a database that loses corrections, and a fluent broadcast cannot compensate
for a claim with no traceable evidence.

| Priority | Task | Phase |
|---|---|---|
| P0 | Run the complete test suite; establish a reproducible baseline. | 0 ✓ |
| P0 | Verify persistence of every core domain entity. | 1 |
| P0 | Define `Article`, `Event`, `Story` separately. | 1 |
| P0 | Keep `Correction` separate from `Evidence`. | 1 |
| P0 | Verify snapshot consistency and historical semantics. | 1, 4 |
| P0 | Verify append-only provenance and correction history. | 1 |
| P1 | Establish idempotent ingestion and background processing. | 2 |
| P1 | Implement reliable article→claim→evidence relationships. | 3 |
| P1 | Add explicit segment and broadcast-session state machines. | 5 |
| P2 | Implement story ranking and editorial scheduling. | 4 |
| P2 | Add script validation and local TTS. | 5 |
| P3 | Introduce personas, listener interaction, advanced presentation. | 6, 7 |
