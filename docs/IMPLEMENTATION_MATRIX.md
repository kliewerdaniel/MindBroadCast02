# Implementation Matrix

Spec section → documentation → phase → current status.

Status: `IMPLEMENTED` (verified by passing tests) · `PARTIAL` (exists with
known defects or missing invariants) · `MISSING` (not built) · `DEFERRED`
(intentionally out of scope for early phases) · `N/A` (governance/documentation
only).

## §1 Executive summary

| Item | Doc | Phase | Status |
|---|---|---|---|
| 18 primary objectives | PROJECT_BRIEF.md | all | N/A |
| Non-goals | PROJECT_BRIEF.md | all | N/A |
| Layer separation (knows / believes / personas / says) | EPISTEMIC_INVARIANTS.md | 1–5 | PARTIAL — models exist, invariants violated (see §5 audit) |

## §2 Product definition

| Item | Doc | Phase | Status |
|---|---|---|---|
| Control loop | PROJECT_BRIEF.md | all | N/A |
| 8 programming modes | EDITORIAL_MODEL.md | 4–5 | MISSING |
| Operating modes (LIVE…STOPPED) | STATE_MACHINES.md §1 | 1 (lifecycle), 5 | MISSING |

## §3 Architectural principles

| Item | Doc | Phase | Status |
|---|---|---|---|
| Local-first execution | ARCHITECTURE.md P1 | all | N/A |
| Deterministic boundaries | ARCHITECTURE.md P2 | all | N/A |
| Append-only provenance | PERSISTENCE_AND_EVENTS.md | 1 | PARTIAL — `test_story_cannot_be_updated_directly` fails |
| Explicit epistemic status | EPISTEMIC_INVARIANTS.md | 1, 3 | PARTIAL — enum set incomplete |
| Observation / interpretation / publication separation | EPISTEMIC_INVARIANTS.md | 3, 5 | MISSING (no generation layer yet) |

## §4 System architecture

| Item | Doc | Phase | Status |
|---|---|---|---|
| Layered architecture | ARCHITECTURE.md | all | N/A |
| 20 logical modules | ARCHITECTURE.md table | 1–7 | `persistence` PARTIAL; all others MISSING |
| Process topology | ARCHITECTURE.md §4.3 | 1 | MISSING (no application lifecycle module) |
| Async I/O, no transaction across model calls | PERSISTENCE_AND_EVENTS.md | 1 | PARTIAL — store is async; transaction discipline unverified |

## §5 Canonical data model

| Entity | Doc | Phase | Status |
|---|---|---|---|
| Record conventions | DOMAIN_MODEL.md §5.1 | 1 | PARTIAL |
| Three time axes | DOMAIN_MODEL.md §5.9.1 | 1 | PARTIAL |
| `Source` | DOMAIN_MODEL.md §5.2 | 1 | **MISSING** |
| `Article` | DOMAIN_MODEL.md §5.3 | 1 | **MISSING** — `Event` conflates it |
| Article versioning | DOMAIN_MODEL.md §5.3.1 | 1 | MISSING |
| `Entity` | DOMAIN_MODEL.md §5.4 | 1 | PARTIAL — no status, thin fields |
| `Event` | DOMAIN_MODEL.md §5.5 | 1 | PARTIAL — no status field |
| `Story` | DOMAIN_MODEL.md §5.5.1 | 1 | PARTIAL — thin |
| `Claim` + types | DOMAIN_MODEL.md §5.6 | 1 | PARTIAL — no `claim_type` |
| Claim status set | DOMAIN_MODEL.md §5.6.2 | 1 | PARTIAL — 4 of 8 statuses |
| `Evidence` | DOMAIN_MODEL.md §5.7 | 1 | PARTIAL |
| `ClaimEvidence` association | DOMAIN_MODEL.md §5.7 | 1 | **MISSING** |
| `Correction` | DOMAIN_MODEL.md §5.8 | 1 | **VIOLATED** — stored as Evidence |
| `WorldSnapshot` | DOMAIN_MODEL.md §5.9 | 1 | **VIOLATED** — counts + titles only |
| Snapshot atomic boundary | DOMAIN_MODEL.md §5.9.1 | 1 | MISSING |
| Provenance graph | DOMAIN_MODEL.md §5.10 | 1, 3 | MISSING — no association table |
| CKU | DOMAIN_MODEL.md §8.3 | 3+ | DEFERRED (spec: interface over domain objects) |

## §6 Persistence, events, state

| Item | Doc | Phase | Status |
|---|---|---|---|
| SQLite practices (FK, WAL, busy timeout, migrations) | PERSISTENCE_AND_EVENTS.md | 1 | PARTIAL — WAL present; FK/uniqueness gaps |
| Domain event log + envelope | PERSISTENCE_AND_EVENTS.md §6.2 | 1 | **MISSING** |
| Event sourcing hybrid | PERSISTENCE_AND_EVENTS.md §6.3 | 1 | MISSING |
| Transactional outbox | PERSISTENCE_AND_EVENTS.md §6.4 | 1 | **MISSING** |
| Idempotency keys | PERSISTENCE_AND_EVENTS.md §6.5 | 1 | **MISSING** |
| DB/filesystem consistency (artifact lifecycle) | PERSISTENCE_AND_EVENTS.md §6.6 | 1, 5 | MISSING |
| Startup recovery (10 steps) | PERSISTENCE_AND_EVENTS.md §6.7 | 1 | **MISSING** |

## §7 Ingestion and source management

| Item | Doc | Phase | Status |
|---|---|---|---|
| Source adapters + interface | ARCHITECTURE.md §Provider contracts | 2 | MISSING |
| Polling, backoff, backpressure | EDITORIAL_MODEL.md §Priority classes | 2 | MISSING |
| Canonicalization | DOMAIN_MODEL.md §5.3 | 2 | MISSING |
| 5-level deduplication | EPISTEMIC_INVARIANTS.md I8, I9 | 2 | MISSING |
| External-content security (SSRF, injection) | ARCHITECTURE.md §Security | 2 | MISSING |
| Ingestion acceptance criteria (10) | PHASE_1_PLAN.md analog | 2 | — |

## §8 Knowledge compiler

| Item | Doc | Phase | Status |
|---|---|---|---|
| Pass 1 structural normalization | ARCHITECTURE.md | 3 | MISSING |
| Pass 2 entity extraction | ARCHITECTURE.md | 3 | MISSING |
| Pass 3 claim extraction (attribution preserved) | EPISTEMIC_INVARIANTS.md I2 | 3 | MISSING |
| Pass 4 evidence linking | ARCHITECTURE.md | 3 | MISSING |
| Pass 5 entity resolution (candidate ≠ merge) | DOMAIN_MODEL.md §5.4 | 3 | MISSING |
| Pass 6 event resolution | ARCHITECTURE.md | 3 | MISSING |
| Pass 7 contradiction / corroboration | EPISTEMIC_INVARIANTS.md I10 | 3 | MISSING |
| Pass 8 knowledge integration | PERSISTENCE_AND_EVENTS.md | 3 | MISSING |
| Embeddings + semantic retrieval | ARCHITECTURE.md §14.2 | 3–4 | MISSING |
| Versioned compilation + invalidation | PERSISTENCE_AND_EVENTS.md §Migrations | 3 | MISSING |

## §9 World state

| Item | Doc | Phase | Status |
|---|---|---|---|
| Current vs historical view | DOMAIN_MODEL.md §5.9 | 4 | MISSING |
| Material change detection | EDITORIAL_MODEL.md §Novelty | 4 | MISSING |
| Temporal reasoning (6 time signals) | EDITORIAL_MODEL.md §Temporal velocity | 4 | MISSING |
| Uncertainty attaches where it occurs | EPISTEMIC_INVARIANTS.md I11 | 4 | MISSING |
| World-state update procedure (9 steps) | PERSISTENCE_AND_EVENTS.md | 4 | MISSING |

## §10 Story intelligence

| Item | Doc | Phase | Status |
|---|---|---|---|
| Ranking function P(s,t) | EDITORIAL_MODEL.md | 4 | MISSING |
| Importance (not attention) | EDITORIAL_MODEL.md §Importance | 4 | MISSING |
| Novelty (5 components) | EDITORIAL_MODEL.md §Novelty | 4 | MISSING |
| Temporal velocity (decay model) | EDITORIAL_MODEL.md §Temporal velocity | 4 | MISSING |
| Graph centrality (normalized, contextual) | EDITORIAL_MODEL.md §Centrality | 4 | MISSING |
| Evidence quality classes | EDITORIAL_MODEL.md §Evidence quality | 4 | MISSING |
| Audience relevance (bounded, no feedback loop) | EDITORIAL_MODEL.md §Audience relevance | 4, 7 | MISSING |
| Coverage debt | EDITORIAL_MODEL.md §Coverage debt | 4 | MISSING |
| Coverage saturation | EDITORIAL_MODEL.md §Coverage saturation | 4 | MISSING |
| Selection objective | EDITORIAL_MODEL.md §Selection objective | 4 | MISSING |
| Explainable selection record | EDITORIAL_MODEL.md §Explainable selection | 4 | MISSING |
| Fairness / bias monitoring | EDITORIAL_MODEL.md §Fairness | 4, 8 | MISSING |

## §11 Editorial control plane

| Item | Doc | Phase | Status |
|---|---|---|---|
| BroadcastPlan / PlannedSegment | DOMAIN_MODEL.md §11.2 | 4–5 | MISSING |
| 12 segment types | EDITORIAL_MODEL.md §Segment types | 5 | MISSING |
| Duration policy + estimator | EDITORIAL_MODEL.md §Duration | 5 | MISSING |
| Transitions (no invented connections) | EDITORIAL_MODEL.md §Transitions | 5 | MISSING |
| Interruptions (CONTINUE/QUEUE_NEXT/INTERRUPT) | STATE_MACHINES.md §4 | 5 | MISSING |
| Planner state machine | STATE_MACHINES.md §2 | 5 | MISSING |

## §12 Personas

| Item | Doc | Phase | Status |
|---|---|---|---|
| Persona config + versioning | DOMAIN_MODEL.md §12.2 | 6 | MISSING |
| Epistemology / personality separation | EPISTEMIC_INVARIANTS.md I6 | 6 | MISSING |
| Independent first-pass analysis | EPISTEMIC_INVARIANTS.md I17 | 6 | MISSING |
| Disagreement model | DOMAIN_MODEL.md §12.5 | 6 | MISSING |
| Persona memory (5 kinds) | ARCHITECTURE.md | 6 | MISSING |
| Persona consistency evaluation | EPISTEMIC_INVARIANTS.md I6 | 6, 8 | MISSING |

## §13 Broadcast director

| Item | Doc | Phase | Status |
|---|---|---|---|
| Director responsibilities | ARCHITECTURE.md | 5 | MISSING |
| BroadcastSession | DOMAIN_MODEL.md §13.2 | 5 | MISSING |
| Segment lifecycle (11 states) | STATE_MACHINES.md §3 | 5 | MISSING |
| Generated ≠ validated ≠ delivered | EPISTEMIC_INVARIANTS.md I12 | 5 | MISSING |
| Interrupt-safe audio, stale-chunk rejection | STATE_MACHINES.md §3 | 5 | MISSING |
| No exactly-once playback assumption | STATE_MACHINES.md §3 | 5 | MISSING |

## §14 Language-model orchestration

| Item | Doc | Phase | Status |
|---|---|---|---|
| Provider abstraction | ARCHITECTURE.md §Provider contracts | 5 | MISSING |
| Model routing by task | ARCHITECTURE.md §Model routing | 5 | MISSING |
| GenerationRequest contract | ARCHITECTURE.md | 5 | MISSING |
| Context assembly (bounded, versioned) | ARCHITECTURE.md | 5 | MISSING |
| Injection / tool restrictions | ARCHITECTURE.md §Security | 5 | MISSING |
| Retry / timeout / failure recording | ARCHITECTURE.md | 5 | MISSING |
| Correlated-error disclosure | EPISTEMIC_INVARIANTS.md I7 | 6 | MISSING |

## §15 Script construction and validation

| Item | Doc | Phase | Status |
|---|---|---|---|
| BroadcastScript / ScriptTurn artifacts | DOMAIN_MODEL.md §15.1 | 5 | MISSING |
| Epistemic labeling (11 categories) | EPISTEMIC_INVARIANTS.md | 5 | MISSING |
| Factual claims must have provenance | EPISTEMIC_INVARIANTS.md I14 | 5 | MISSING |
| 8-stage validation pipeline | DECISIONS.md ADR-005 | 5 | MISSING |
| Validation outcomes (5) | STATE_MACHINES.md §3 | 5 | MISSING |
| Independent verification | EPISTEMIC_INVARIANTS.md I13 | 5 | MISSING |
| Post-broadcast correction workflow | STATE_MACHINES.md §7 | 5, 8 | MISSING |

## §16 TTS and audio

| Item | Doc | Phase | Status |
|---|---|---|---|
| TTS provider interface | ARCHITECTURE.md §Provider contracts | 5 | MISSING |
| VoiceProfile (separate from persona) | DOMAIN_MODEL.md §16.3 | 5, 6 | MISSING |
| Chunking and latency | ARCHITECTURE.md | 5 | MISSING |
| Audio formats + normalization | ARCHITECTURE.md | 5 | MISSING |
| Audio buffering (9 capabilities) | ARCHITECTURE.md | 5 | MISSING |
| Audio artifact storage + retention | DOMAIN_MODEL.md §16.7 | 5 | MISSING |
| Continuous stream, separate audio/metadata transports | ARCHITECTURE.md | 5 | MISSING |

## §17 Audience

| Item | Doc | Phase | Status |
|---|---|---|---|
| ListenerMessage model | DOMAIN_MODEL.md §17.2 | 7 | MISSING |
| Message processing pipeline | STATE_MACHINES.md §8 | 7 | MISSING |
| Feedback signals (noisy evidence) | EDITORIAL_MODEL.md §Audience relevance | 7 | MISSING |
| Anti-manipulation controls | ARCHITECTURE.md §Security | 7 | MISSING |

## §18 UI and operator experience

| Item | Doc | Phase | Status |
|---|---|---|---|
| Dashboard (4 immediate questions) | ARCHITECTURE.md §Observability | 7 | MISSING |
| Live transcript | ARCHITECTURE.md | 5, 7 | MISSING |
| SHOW ANALYSIS | ARCHITECTURE.md | 7 | MISSING |
| World explorer | ARCHITECTURE.md | 7 | MISSING |
| Operator controls | ARCHITECTURE.md | 7 | MISSING |
| Accessibility | ARCHITECTURE.md | 7 | MISSING |

## §19 API and events

| Item | Doc | Phase | Status |
|---|---|---|---|
| 18 core endpoints | ARCHITECTURE.md | 7 | MISSING |
| SSE metadata events (10 types) | ARCHITECTURE.md | 7 | MISSING |
| Audio streaming endpoint | ARCHITECTURE.md | 5, 7 | MISSING |
| API versioning + structured errors | ARCHITECTURE.md | 7 | MISSING |

## §20–§23 Security, observability, performance, deployment

| Item | Doc | Phase | Status |
|---|---|---|---|
| Threat model | ARCHITECTURE.md §Security | 2–7 | N/A (governance) |
| Least privilege | ARCHITECTURE.md §Security | all | N/A |
| Loopback default | ARCHITECTURE.md §Security | 7 | MISSING |
| Secrets handling | ARCHITECTURE.md §Security | 5 | MISSING |
| Metrics per subsystem | ARCHITECTURE.md §Observability | 1+ | MISSING |
| Liveness vs readiness | ARCHITECTURE.md §Observability | 1 | MISSING |
| Priority classes (0–4) | EDITORIAL_MODEL.md §Priority classes | 4 | MISSING |
| Backpressure policy | EDITORIAL_MODEL.md | 2 | MISSING |
| Caching + invalidation | ARCHITECTURE.md | 3–5 | MISSING |
| Config hierarchy + validation | ARCHITECTURE.md §Storage layout | 1 | MISSING |
| Backup / restore / upgrade | PERSISTENCE_AND_EVENTS.md | 1, 8 | MISSING |

## §25–§26 Roadmap and remediation

| Item | Doc | Phase | Status |
|---|---|---|---|
| Phases 0–9 | ROADMAP.md | — | Phase 0 COMPLETE |
| Remediation priorities P0–P3 | ROADMAP.md §Remediation | 1–7 | Phase 0 items done |
| Baseline test results | PHASE_0_AUDIT.md §2 | 0 | **REPRODUCED: 49 passed / 27 failed** |

## §27–§31 Decisions, improvement, open questions, done

| Item | Doc | Phase | Status |
|---|---|---|---|
| 6 architectural recommendations | DECISIONS.md | — | 3 ratified, 3 pending |
| Recursive improvement layer | EDITORIAL_MODEL.md §Recursive | 9 | DEFERRED |
| 16 recommended defaults | PROJECT_BRIEF.md §Orientation | — | 12 ratified, 4 pending (OPEN_QUESTIONS.md) |
| Definition of Done | ACCEPTANCE_CRITERIA.md | — | 0 of 10 gates met |
| Final architectural thesis | PROJECT_BRIEF.md | — | N/A |

## Summary

| Status | Count (approx.) |
|---|---|
| IMPLEMENTED | 0 verified subsystem gates met |
| PARTIAL | ~12 domain objects and infrastructure items |
| MISSING | ~70 spec features across 14 subsystems |
| DEFERRED | CKU, recursive improvement, external radio |
| VIOLATED | 3 (Correction-as-Evidence, snapshot-as-aggregation, append-only) |

The gap between the specification and the implementation is the project. Phase 1
closes the three violations and the P0 domain gaps.
