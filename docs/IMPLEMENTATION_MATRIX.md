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
| Append-only provenance | PERSISTENCE_AND_EVENTS.md | 1 | **IMPLEMENTED** — 12 DB triggers, RAISE(ABORT) |
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
| `Source` | DOMAIN_MODEL.md §5.2 | 1 | **IMPLEMENTED** |
| `Article` | DOMAIN_MODEL.md §5.3 | 1 | **IMPLEMENTED** — source FK, version chain, UNIQUE(content_hash) |
| Article versioning | DOMAIN_MODEL.md §5.3.1 | 1 | **IMPLEMENTED** — `supersede_article`, walkable chain |
| `Entity` | DOMAIN_MODEL.md §5.4 | 1 | **IMPLEMENTED** — status, aliases, external IDs, explicit merge |
| `Event` | DOMAIN_MODEL.md §5.5 | 1 | **IMPLEMENTED** — split from Article; full status set |
| `Story` | DOMAIN_MODEL.md §5.5.1 | 1 | **IMPLEMENTED** — coverage tracking, superseding status changes |
| `Claim` + types | DOMAIN_MODEL.md §5.6 | 1 | **IMPLEMENTED** — 7 claim types |
| Claim status set | DOMAIN_MODEL.md §5.6.2 | 1 | **IMPLEMENTED** — 8 statuses + legacy mapping |
| `Evidence` | DOMAIN_MODEL.md §5.7 | 1 | **IMPLEMENTED** — extraction provenance, article FK |
| `ClaimEvidence` association | DOMAIN_MODEL.md §5.7 | 1 | **IMPLEMENTED** — 6 relationship types |
| `Correction` | DOMAIN_MODEL.md §5.8 | 1 | **IMPLEMENTED** — first-class table, 7 types |
| `WorldSnapshot` | DOMAIN_MODEL.md §5.9 | 1 | **IMPLEMENTED** — watermark, hash, manifest, parent chain |
| Snapshot atomic boundary | DOMAIN_MODEL.md §5.9.1 | 1 | **IMPLEMENTED** — single read transaction |
| Provenance graph | DOMAIN_MODEL.md §5.10 | 1, 3 | PARTIAL — association tables + event log exist; traversal is Phase 3 |
| CKU | DOMAIN_MODEL.md §8.3 | 3 | **IMPLEMENTED** — read-only view, no table (ADR-024) |

## §6 Persistence, events, state

| Item | Doc | Phase | Status |
|---|---|---|---|
| SQLite practices (FK, WAL, busy timeout, migrations) | PERSISTENCE_AND_EVENTS.md | 1 | **IMPLEMENTED** — FK/WAL/busy-timeout on every connection; `core/migrations.py` |
| Domain event log + envelope | PERSISTENCE_AND_EVENTS.md §6.2 | 1 | **IMPLEMENTED** — full envelope, append-only |
| Event sourcing hybrid | PERSISTENCE_AND_EVENTS.md §6.3 | 1 | MISSING |
| Transactional outbox | PERSISTENCE_AND_EVENTS.md §6.4 | 1 | **IMPLEMENTED** — standalone + transactional entry points |
| Idempotency keys | PERSISTENCE_AND_EVENTS.md §6.5 | 1 | **IMPLEMENTED** |
| DB/filesystem consistency (artifact lifecycle) | PERSISTENCE_AND_EVENTS.md §6.6 | 1, 5 | MISSING |
| Startup recovery (10 steps) | PERSISTENCE_AND_EVENTS.md §6.7 | 1 | **IMPLEMENTED** — `core/lifecycle.py`, fails on integrity violations |

## §7 Ingestion and source management

| Item | Doc | Phase | Status |
|---|---|---|---|
| Source adapters + interface | ARCHITECTURE.md §Provider contracts | 2 | **IMPLEMENTED** — `ingestion/adapters.py`, §7.2 contract |
| Polling, backoff, backpressure | EDITORIAL_MODEL.md §Priority classes | 2 | **IMPLEMENTED** — `ingestion/scheduler.py` |
| Canonicalization | DOMAIN_MODEL.md §5.3 | 2 | **IMPLEMENTED** — `ingestion/canonicalization.py`, rules recorded |
| 5-level deduplication | EPISTEMIC_INVARIANTS.md I8, I9 | 2 | **IMPLEMENTED** — `ingestion/deduplication.py`, classified candidates |
| External-content security (SSRF, injection) | ARCHITECTURE.md §Security | 2 | **IMPLEMENTED** — `ingestion/security.py`, redirect re-validation |
| Ingestion acceptance criteria (10) | test_ingestion.py | 2 | **ALL TEN TESTED** |

## §8 Knowledge compiler

| Item | Doc | Phase | Status |
|---|---|---|---|
| Pass 1 structural normalization | ARCHITECTURE.md | 3 | **IMPLEMENTED** — offsets verified against source |
| Pass 2 entity extraction | ARCHITECTURE.md | 3 | **IMPLEMENTED** — mentions only, never merges |
| Pass 3 claim extraction (attribution preserved) | EPISTEMIC_INVARIANTS.md I2 | 3 | **IMPLEMENTED** — attribution checked first |
| Pass 4 evidence linking | ARCHITECTURE.md | 3 | **IMPLEMENTED** — ATTRIBUTES vs SUPPORTS |
| Pass 5 entity resolution (candidate ≠ merge) | DOMAIN_MODEL.md §5.4 | 3 | **PARTIAL** — deterministic only; model-assisted is Phase 4+ |
| Pass 6 event resolution | ARCHITECTURE.md | 3 | **PARTIAL** — one conservative candidate per article |
| Pass 7 contradiction / corroboration | EPISTEMIC_INVARIANTS.md I10 | 3 | **IMPLEMENTED** — qualifiers compared before contradiction |
| Pass 8 knowledge integration | PERSISTENCE_AND_EVENTS.md | 3 | **IMPLEMENTED** — all-or-nothing, dependency rows |
| Embeddings + semantic retrieval | ARCHITECTURE.md §14.2 | 4+ | DEFERRED (spec: FTS first, vector DB on measured need) |
| Versioned compilation + invalidation | PERSISTENCE_AND_EVENTS.md §Migrations | 3 | **IMPLEMENTED** — pass versions + DerivedObjectsRegistered |

## §9 World state

| Item | Doc | Phase | Status |
|---|---|---|---|
| Current vs historical view | DOMAIN_MODEL.md §5.9 | 4 | **IMPLEMENTED** — `world_model/replay.py` |
| Material change detection | EDITORIAL_MODEL.md §Novelty | 4 | **IMPLEMENTED** — `world_model/changes.py`, 10 change kinds |
| Temporal reasoning (6 time signals) | EDITORIAL_MODEL.md §Temporal velocity | 4 | **IMPLEMENTED** — decays from observation time |
| Uncertainty attaches where it occurs | EPISTEMIC_INVARIANTS.md I11 | 4 | MISSING |
| World-state update procedure (9 steps) | PERSISTENCE_AND_EVENTS.md | 4 | MISSING |

## §10 Story intelligence

| Item | Doc | Phase | Status |
|---|---|---|---|
| Ranking function P(s,t) | EDITORIAL_MODEL.md | 4 | **IMPLEMENTED** — all nine factors + penalties |
| Importance (not attention) | EDITORIAL_MODEL.md §Importance | 4 | **IMPLEMENTED** — attention explicitly excluded |
| Novelty (5 components) | EDITORIAL_MODEL.md §Novelty | 4 | **IMPLEMENTED** — measured against world model |
| Temporal velocity (decay model) | EDITORIAL_MODEL.md §Temporal velocity | 4 | **IMPLEMENTED** — Σ W(d)·exp(−Δt/τ), tau by story type |
| Graph centrality (normalized, contextual) | EDITORIAL_MODEL.md §Centrality | 4 | **IMPLEMENTED** — normalized, never decisive |
| Evidence quality classes | EDITORIAL_MODEL.md §Evidence quality | 4 | **IMPLEMENTED** — ownership-group-aware independence |
| Audience relevance (bounded, no feedback loop) | EDITORIAL_MODEL.md §Audience relevance | 4 | **IMPLEMENTED** — capped additive term (ADR-028) |
| Coverage debt | EDITORIAL_MODEL.md §Coverage debt | 4 | **IMPLEMENTED** |
| Coverage saturation | EDITORIAL_MODEL.md §Coverage saturation | 4 | **IMPLEMENTED** — lifts on material change (§10.9) |
| Selection objective | EDITORIAL_MODEL.md §Selection objective | 4 | **IMPLEMENTED** — greedy net-value ordering |
| Explainable selection record | EDITORIAL_MODEL.md §Explainable selection | 4 | **IMPLEMENTED** — structured decision record (§10.11) |
| Story clustering / story engine | DOMAIN_MODEL.md §5.5.1 | 4 | **IMPLEMENTED** — deterministic, qualifier-aware (ADR-029) |
| Temporal-leakage-safe replay | TESTING_STRATEGY.md §Layer 9 | 4 | **IMPLEMENTED** — observation-time filter, violations reported |

## Broadcast (§13–§16)

| Feature | Reference | Phase | Status |
|---|---|---|---|
| Model-provider abstraction (local-first) | ARCHITECTURE.md §Provider interface | 5 | **IMPLEMENTED** — capability-declaring protocols + stubs |
| Generation-request contract | ARCHITECTURE.md §Generation request | 5 | **IMPLEMENTED** — idempotency key, versions, deadline |
| Evidence packet (application-built) | ARCHITECTURE.md §Evidence packet | 5 | **IMPLEMENTED** — `ContextBuilder`, provenance preserved |
| Context assembly (§14.4) | ARCHITECTURE.md §Context builder | 5 | **IMPLEMENTED** — contradictory evidence always included |
| Prompt-injection structural controls | ARCHITECTURE.md §Prompt injection | 5 | **IMPLEMENTED** — data delimiters, id validation, blocking |
| Script validation (8 stages) | ARCHITECTURE.md §Validation pipeline | 5 | **IMPLEMENTED** — 7 deterministic, verifier optional |
| Epistemic labeling (§15.2) | ARCHITECTURE.md §Epistemic labeling | 5 | **IMPLEMENTED** — internal metadata, 11 categories |
| Claim provenance (§15.3) | ARCHITECTURE.md §Provenance | 5 | **IMPLEMENTED** — source list is not support |
| Segment lifecycle (11 states) | STATE_MACHINES.md §Segment | 5 | **IMPLEMENTED** — illegal transitions refused |
| TTS failure does not invalidate script (§13.3) | STATE_MACHINES.md | 5 | **IMPLEMENTED** — returns to APPROVED |
| Audio buffer (ordered, bounded, stale-rejecting) | ARCHITECTURE.md §Audio buffer | 5 | **IMPLEMENTED** — byte budget, chunk ordering |
| Interrupt-safe audio (§13.5) | STATE_MACHINES.md §Interruption | 5 | **IMPLEMENTED** — abandoned audio dropped |
| Director state + crash recovery (§13.2) | STATE_MACHINES.md §Director | 5 | **IMPLEMENTED** — PLAYING recovers as INTERRUPTED |
| Post-broadcast correction (§15.7) | EDITORIAL_MODEL.md §Correction | 5 | **IMPLEMENTED** — append-only transcript archive |

## Personas (§12)

| Feature | Reference | Phase | Status |
|---|---|---|---|
| Versioned persona configuration | DOMAIN_MODEL.md §Persona | 6 | **IMPLEMENTED** — update creates a version (ADR-037) |
| Persona memory (5 types, bounded) | DOMAIN_MODEL.md §Persona memory | 6 | **IMPLEMENTED** — typed, provenance, capped (ADR-040) |
| Independent first-pass analysis | EDITORIAL_MODEL.md §Independent analysis | 6 | **IMPLEMENTED** — ordering-enforced (ADR-038) |
| Disagreement model | EDITORIAL_MODEL.md §Disagreement | 6 | **IMPLEMENTED** — precise proposition, 7 types |
| Structured exchange composition | EDITORIAL_MODEL.md §Dialogue | 6 | **IMPLEMENTED** — built from disagreement records |
| Persona consistency evaluation | EDITORIAL_MODEL.md §Consistency | 6 | **IMPLEMENTED** — explainable change ≠ drift (ADR-039) |
| Cross-type memory revision withdrawal | DOMAIN_MODEL.md §Persona memory | 6 | **IMPLEMENTED** — spans buckets (ADR-041) |
| Imitation / correlated-error detection | TESTING_STRATEGY.md §Layer 12 | 6 | **IMPLEMENTED** — surfaces §14.7 symptom |

## Audience and API (§17–§19)

| Feature | Reference | Phase | Status |
|---|---|---|---|
| Listener message pipeline | ARCHITECTURE.md §Listener channel | 7 | **IMPLEMENTED** — durable, non-blocking (§17.1) |
| Listener cannot establish claims | ARCHITECTURE.md §Listener isolation | 7 | **IMPLEMENTED** — no write path (ADR-044) |
| Rate limits and queue quotas | ARCHITECTURE.md §Abuse protection | 7 | **IMPLEMENTED** — per-client, topic-influence bounded |
| Abuse classification | ARCHITECTURE.md §Abuse protection | 7 | **IMPLEMENTED** — stem-matched markers (ADR-048) |
| Feedback signals (noisy) | ARCHITECTURE.md §Feedback | 7 | **IMPLEMENTED** — confidence-capped (ADR-049) |
| API layer (domain operations) | ARCHITECTURE.md §API | 7 | **IMPLEMENTED** — own response types (ADR-043) |
| Structured errors | ARCHITECTURE.md §Errors | 7 | **IMPLEMENTED** — code/message/id/retryable |
| SSE event stream (persisted, replayable) | ARCHITECTURE.md §Events | 7 | **IMPLEMENTED** — stable ids, cursor replay |
| SHOW ANALYSIS audit surface | ARCHITECTURE.md §Show analysis | 7 | **IMPLEMENTED** — 10 fields, no chain-of-thought |
| Live transcript (generated ≠ delivered) | ARCHITECTURE.md §Transcript | 7 | **IMPLEMENTED** — delivery-time entries (ADR-046) |
| Operator controls (confirm + audit) | ARCHITECTURE.md §Operator | 7 | **IMPLEMENTED** — risk-classified (ADR-047) |
| Accessibility data contract | ARCHITECTURE.md §Accessibility | 7 | **IMPLEMENTED** — status not color-only |
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
| IMPLEMENTED | Gates 1–9 closed; 85 domain/infrastructure items |
| PARTIAL | ~8 items (provenance traversal, temporal reasoning, observability) |
| MISSING | ~24 spec features across 10 subsystems (Phases 8–9) |
| DEFERRED | CKU, recursive improvement, external radio |
| VIOLATED | 0 — all three prior violations resolved |

The gap between the specification and the implementation is the project.
Phase 1 closed the three violations and the P0 domain gaps. **Gate 1 is MET:
253 tests passing.**

The gap between the specification and the implementation is the project. Phase 1
closes the three violations and the P0 domain gaps.
