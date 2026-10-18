# MindBroadCast02 — Documentation Index

Documentation set for the development of **MindBroadcast**: a persistent,
autonomous, news-driven intelligence and broadcasting system.

**Source of truth:** the specification published 2026-10-12
(`https://www.danielkliewer.com/blog/2026-10-12-mindbroadcast`, v1.0, 31 sections).
These docs decompose that spec into implementable phases. Where the spec and these
docs disagree, the spec wins; raise it as a decision in DECISIONS.md.

**Conventions**

- Sections cite the spec as `§N` (e.g. `§5.6` = spec section 5.6, Claim).
- Requirements use RFC-2119 vocabulary: **MUST** (blocking invariant),
  **SHOULD** (default unless a decision overrides), **MAY** (optional).
- Every phase ends in an exit gate with real tests. A feature is done when its
  gate passes on a real run, not when a happy path was demonstrated once.
- Fail closed: when a check cannot be performed, the conservative outcome
  (do not broadcast / do not commit) is the default.

## Reading order

| # | Doc | Purpose |
|---|---|---|
| 1 | [PROJECT_BRIEF.md](PROJECT_BRIEF.md) | Objectives, non-goals, operating modes, control loop. Read first. |
| 2 | [ARCHITECTURE.md](ARCHITECTURE.md) | Layers, module boundaries, process topology, provider contracts. |
| 3 | [DOMAIN_MODEL.md](DOMAIN_MODEL.md) | Canonical data model: Source, Article, Entity, Event, Story, Claim, Evidence, Correction, WorldSnapshot, CKU. |
| 4 | [EPISTEMIC_INVARIANTS.md](EPISTEMIC_INVARIANTS.md) | The distinctions the system MUST preserve. The negative architecture. |
| 5 | [PERSISTENCE_AND_EVENTS.md](PERSISTENCE_AND_EVENTS.md) | SQLite rules, event log, transactional outbox, idempotency, artifact lifecycle, recovery. |
| 6 | [STATE_MACHINES.md](STATE_MACHINES.md) | Operating mode, planner, and segment state machines with explicit transition tables. |
| 7 | [EDITORIAL_MODEL.md](EDITORIAL_MODEL.md) | Ranking factors, novelty, coverage debt/saturation, interruptions, explainability. |
| 8 | [TESTING_STRATEGY.md](TESTING_STRATEGY.md) | Test layers, evaluation corpora, failure injection, acceptance gates. |
| 9 | [ROADMAP.md](ROADMAP.md) | Phases 0–9, dependency order, exit criteria, gates. |
| 10 | [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md) | Definition of Done and Gates 1–10. |
| 11 | [IMPLEMENTATION_MATRIX.md](IMPLEMENTATION_MATRIX.md) | Spec section → doc → phase → status. |
| 12 | [DECISIONS.md](DECISIONS.md) | ADR log. Ratify before code, per ADR-gated engineering. |
| 13 | [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) | Unresolved decisions and their recommended defaults. |
| 14 | [DEPLOYMENT.md](DEPLOYMENT.md) | How to run the station on each OS. |
| 15 | [RELEASE.md](RELEASE.md) | Release pipeline and artifact walkthrough. |

### Archive

Executed phase plans and per-phase ADRs are in
[`docs/archive/`](archive/). They are historical records of decisions
taken during implementation, not live reference:

| Doc | Contents |
|---|---|
| [archive/PHASE_0_AUDIT.md](archive/PHASE_0_AUDIT.md) | Current-state audit against the prior implementation. |
| [archive/PHASE_1_PLAN.md](archive/PHASE_1_PLAN.md) | Persistence and domain integrity plan. |
| [archive/PHASE_2_PLAN.md](archive/PHASE_2_PLAN.md) | Ingestion and source normalization plan. |
| [archive/PHASE_3_PLAN.md](archive/PHASE_3_PLAN.md) | Knowledge compiler plan. |
| [archive/PHASE_4_PLAN.md](archive/PHASE_4_PLAN.md) | World model and story intelligence plan. |
| [archive/PHASE_5_PLAN.md](archive/PHASE_5_PLAN.md) | Single-persona broadcasting plan. |
| [archive/PHASE_6_PLAN.md](archive/PHASE_6_PLAN.md) | Multi-persona dialogue plan. |
| [archive/PHASE_7_PLAN.md](archive/PHASE_7_PLAN.md) | Live audience and operator plan. |
| [archive/PHASE_8_PLAN.md](archive/PHASE_8_PLAN.md) | Reliability and streaming plan. |
| [archive/DECISIONS_PHASE1.md](archive/DECISIONS_PHASE1.md) | ADRs 11–14: Phase 1 decisions. |
| [archive/DECISIONS_PHASE2.md](archive/DECISIONS_PHASE2.md) | ADRs 15–19: Phase 2 decisions. |
| [archive/DECISIONS_PHASE3.md](archive/DECISIONS_PHASE3.md) | ADRs 20–25: Phase 3 decisions. |
| [archive/DECISIONS_PHASE4.md](archive/DECISIONS_PHASE4.md) | ADRs 26–30: Phase 4 decisions. |
| [archive/DECISIONS_PHASE5.md](archive/DECISIONS_PHASE5.md) | ADRs 31–36: Phase 5 decisions. |
| [archive/DECISIONS_PHASE6.md](archive/DECISIONS_PHASE6.md) | ADRs 37–42: Phase 6 decisions. |
| [archive/DECISIONS_PHASE7.md](archive/DECISIONS_PHASE7.md) | ADRs 43–49: Phase 7 decisions. |
| [archive/DECISIONS_PHASE8.md](archive/DECISIONS_PHASE8.md) | ADRs 50–56: Phase 8 decisions. |

## Status legend

- `IMPLEMENTED` — exists and is verified by passing tests.
- `PARTIAL` — exists but has known defects or missing invariants.
- `MISSING` — not built.
- `DEFERRED` — intentionally out of scope for early phases.

**Current status: Phases 0–8 complete — ALL GATES MET, 1032 tests passing**
The §25 roadmap is complete. Baseline was 49 pass / 27 fail.
(baseline was 49 pass / 27 fail). See ACCEPTANCE_CRITERIA.md §"Gates 4 and 5 evidence".
