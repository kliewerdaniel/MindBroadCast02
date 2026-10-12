# MindBroadCast02

A persistent, autonomous, news-driven intelligence and broadcasting system.

**Status:** Phases 0–8 complete. **All gates MET.**
Baseline was 49 passed / 27 failed; now **903 passed / 0 failed**.

## What this is

MindBroadcast continuously transforms incoming information about the world into
an evolving stream of spoken analysis, commentary, debate, and explanation. It
maintains an internal model of the world, tracks story evolution, evaluates
competing claims, detects material changes, and decides what to communicate
next — while preserving the distinction between **evidence** and **opinion**.

It is not a podcast generator that renders a fixed script. It is a
continuously running information system.

## The core principle

> The world changes, the system observes those changes, its internal model is
> updated, its perspectives interpret the changes, and its broadcast
> communicates those interpretations **without erasing the distinction between
> evidence and opinion.**

> **Models propose. Schemas constrain. Services authorize. Transactions commit.
> Evidence records what happened.**

## Documentation

Start with [`docs/README.md`](docs/README.md) for the reading order. The
essential docs:

| Doc | Read it for |
|---|---|
| [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md) | Objectives, non-goals, operating modes, the control loop. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, module boundaries, provider contracts, security posture. |
| [docs/DOMAIN_MODEL.md](docs/DOMAIN_MODEL.md) | The canonical data model — every entity and field. |
| [docs/EPISTEMIC_INVARIANTS.md](docs/EPISTEMIC_INVARIANTS.md) | The 20 distinctions the system MUST preserve. |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phases 0–9 with exit criteria. |
| [docs/PHASE_0_AUDIT.md](docs/PHASE_0_AUDIT.md) | What exists today, with a reproduced test baseline. |
| [docs/PHASE_1_PLAN.md](docs/PHASE_1_PLAN.md) | The next phase, task by task. |
| [docs/ACCEPTANCE_CRITERIA.md](docs/ACCEPTANCE_CRITERIA.md) | Gates 1–10 and the Definition of Done. |
| [docs/DECISIONS.md](docs/DECISIONS.md) | ADR log — ratify before code. |

## Specification

The authoritative source is the published specification (v1.0, 2026-10-12, 31
sections): https://www.danielkliewer.com/blog/2026-10-12-mindbroadcast

These docs decompose that spec into implementable phases and cite it as `§N`.
Where a doc and the spec disagree, the spec wins.

## Roadmap at a glance

```
Phase 0  Audit                      ✅ COMPLETE (49 pass / 27 fail baseline)
Phase 1  Persistence + domain       ✅ COMPLETE — Gate 1 MET
Phase 2  Ingestion + dedup          ✅ COMPLETE — Gate 2 MET
Phase 3  Knowledge compilation      ✅ COMPLETE — Gate 3 MET
Phase 4  World model + stories      ✅ COMPLETE — Gates 4, 5 MET
Phase 5  Single-persona broadcast   ⬜ Gate 6
Phase 6  Personas + debate          ⬜
Phase 7  Audience + operator UI     ⬜ Gate 9
Phase 8  Reliability + streaming    ⬜ Gates 7, 10
Phase 9  Recursive improvement      ⬜
```

## Code layout

```
MindBroadCast02/
├── README.md
├── pytest.ini, requirements.txt
├── core/
│   ├── models.py           Domain model (spec §5) + OperatingMode
│   ├── persistence.py      SQLite store: 15 tables, FK/WAL, append-only triggers
│   ├── migrations.py       Versioned migration runner (up/down, ledger)
│   ├── lifecycle.py        Startup recovery (10 steps), mode, shutdown
│   ├── snapshot.py         Snapshot service
│   ├── legacy_import.py    Pre-Phase-1 schema importer (C2)
│   └── ingestion/
│       ├── security.py         SSRF/scheme/size policy, content stripping (§7.6)
│       ├── canonicalization.py Conservative URL + text normalization (§7.4)
│       ├── deduplication.py    Five distinct duplication levels (§7.5)
│       ├── scheduler.py        Polling, backoff, backpressure (§7.3)
│       ├── adapters.py         RSS/Atom adapters (§7.2)
│       ├── fetcher.py          Policy-enforcing HTTP fetch
│       └── pipeline.py         §7.1 flow to durable capture
└── tests/                  357 tests
    ├── test_models.py          55
    ├── test_persistence.py     90
    ├── test_epistemic.py       32
    ├── test_snapshot.py        18
    ├── test_recovery.py        24
    ├── test_migrations.py      34
    ├── test_ingestion.py      104
    ├── test_compiler.py        71
    ├── test_world_model.py     54
    ├── test_broadcast.py       151
    ├── test_personas.py         79
    ├── test_audience_api.py     90
    └── test_reliability.py     101
```

Run the suite: `python3 -m pytest -q`

## Engineering rules

- **The full test suite must pass.** A phase with known failures is not complete.
- **Fail closed.** When a check cannot be performed, the conservative outcome is
  the default.
- **Root cause, not band-aids.** A partial-suppression fix is not a fix.
- **Real verification.** Report actual test output; never fabricate a result.
- **ADR before code.** Ratify the decision, then implement.
- **No push without instruction.** Local commits by default.

## Prior implementation

An earlier implementation exists at `github.com/kliewerdaniel/MindBroadcast`
(~1,078 lines: SQLite persistence, 7 domain dataclasses). Phase 0 audited it and
reproduced its baseline; Phase 1 resolved its defects. See ADR-002 in
`docs/DECISIONS.md` for what was carried forward and what was rebuilt.

Note: this is **not** a code-level continuation of that repo. The schema is new
(15 tables, `SCHEMA_VERSION=2`) and legacy data arrives through
`core/legacy_import.py`, which is tested to preserve every timestamp and keep
corrections out of the evidence table.

## Layout

```
MindBroadCast02/
├── README.md              ← you are here
├── docs/                  ← the documentation set (15 files)
│   ├── README.md              reading order + index
│   ├── PROJECT_BRIEF.md
│   ├── ARCHITECTURE.md
│   ├── DOMAIN_MODEL.md
│   ├── EPISTEMIC_INVARIANTS.md
│   ├── PERSISTENCE_AND_EVENTS.md
│   ├── STATE_MACHINES.md
│   ├── EDITORIAL_MODEL.md
│   ├── TESTING_STRATEGY.md
│   ├── ROADMAP.md
│   ├── PHASE_0_AUDIT.md
│   ├── PHASE_1_PLAN.md
│   ├── ACCEPTANCE_CRITERIA.md
│   ├── DECISIONS.md
│   ├── IMPLEMENTATION_MATRIX.md
│   └── OPEN_QUESTIONS.md
└── (code — Phase 1)
```
