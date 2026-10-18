# Phase 0 Audit — Current State

**Status:** COMPLETE
**Date:** 2026-10-12
**Method:** direct inspection of the prior implementation at
`github.com/kliewerdaniel/MindBroadcast` (branch `master`, last push
2026-10-08), cloned to `~/Projects/.mindbroadcast-reference`, dependencies
installed, full test suite executed.

The spec itself (§26) states the author had not inspected the repository in the
spec conversation and could not verify which known issues remained. **This audit
closes that gap with actual execution.**

## 1. Repository inventory

```
core/__init__.py      0 lines
core/contracts.py   114 lines
core/models.py      182 lines
core/persistence.py 730 lines
core/snapshot.py     52 lines

tests/conftest.py        106
tests/test_epistemic.py  169
tests/test_models.py     221
tests/test_persistence.py 418
tests/test_snapshot.py   151
                       ----
                total 2149 lines

Also present: main.py, feeds.yaml, pytest.ini, requirements.txt,
8 debug*.py scratch scripts, docs/ (42 markdown files)
```

~1,078 lines of production code against a 31-section specification. The
implementation is an early **persistence-layer prototype**, not a broadcasting
system. There is no ingestion, no knowledge compiler, no LLM provider, no TTS,
no audio, no API, no UI.

Environment: Python 3.9.6, pytest 8.4.2, `asyncio_mode = auto` (pytest.ini).

## 2. Test baseline (REPRODUCED)

Command: `python3 -m pytest -q` in the reference checkout.

```
27 failed, 49 passed, 38 warnings in 0.16s
```

This matches the recorded prior state (49 passed / 25 failed — the count moved
from 25 to 27; the categories are identical). **The suite is not green. Phase 1
cannot be declared complete until it is.**

### Failure breakdown

| Category | Count | Root cause |
|---|---|---|
| `AttributeError: 'async_generator' object has no attribute ...` | 19 | Async fixture misuse |
| `AssertionError` (list/filter count mismatches) | 8 | Query or test-expectation bugs |

### Root cause 1 — async fixture misuse (19 of 27 failures)

`tests/conftest.py`:

```python
@pytest.fixture
async def world_store():
    store = WorldStore(db_path=":memory:")
    yield store
```

The fixture is declared `async`, so pytest hands the test the **async generator
object itself**, not the awaited store. Tests then call methods on a generator →
`AttributeError`.

Error signature breakdown:

| Error | Count |
|---|---|
| `'async_generator' object has no attribute 'create_snapshot'` | 9 |
| `... 'add_claim'` | 5 |
| `... 'add_story'` | 2 |
| `... 'add_evidence'` | 2 |
| `... 'add_event'` | 1 |
| **Total** | **19** |

Additionally, a function-scoped `event_loop` fixture is defined:

```python
@pytest.fixture(scope="function")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
```

This is the deprecated pytest-asyncio pattern and conflicts with
`asyncio_mode = auto`.

**Fix:** make `world_store` a synchronous fixture that constructs the store
(WorldStore's constructor is not async), or properly await it. Remove the
custom `event_loop` fixture and rely on `asyncio_mode = auto`. This single fix
should resolve 19 of 27 failures.

### Root cause 2 — assertion mismatches (8 failures)

Tests asserting counts that do not match query results:

| Test | Assertion |
|---|---|
| `test_list_stories` | `assert 4 == 2` |
| `test_list_stories_with_filter` | `assert 3 == 2` |
| `test_list_claims` | `assert 4 == 2` |
| `test_list_entities` | `assert 4 == 2` |
| `test_list_snapshots` | `assert 2 == 1` |
| (3 more assertion failures in the same classes) | — |

Each requires reading the test's fixture setup to determine whether the test
expectation or the query is wrong. **These are logic or fixture-isolation bugs,
not infrastructure bugs** — investigate individually after the fixture fix,
since fixing isolation may change the counts.

## 3. Schema audit

`core/persistence.py` creates seven tables:

```sql
stories, events, claims, evidence, entities, world_snapshots, schema_version
```

Observed problems:

| Issue | Evidence |
|---|---|
| **No `sources` table** | No `Source` model or table anywhere in `core/`. |
| **No `articles` table** | Articles are not modeled; `events` carries `source_url` directly. |
| **No `corrections` table** | `correct_record()` writes an `Evidence` row (see §4). |
| Foreign-key enforcement unclear | Requires reading connection setup — spec §6.1 requires FK enforcement on **every** connection. |
| Only one UNIQUE constraint | `UNIQUE(name, entity_type)` on entities. No uniqueness on canonical URLs or dedup keys. |
| No CHECK constraints | No status enum enforcement at the DB level. |

## 4. Domain model gaps

`core/models.py` defines: `Story`, `Event`, `Claim`, `Evidence`, `Entity`,
`Correction`, `WorldSnapshot`, `SnapshotDiff`.

| Spec requirement | Status |
|---|---|
| `Source` as first-class entity | **MISSING** — not in models.py, not in schema. Articles carry `source_url`. |
| `Article` distinct from `Event` | **MISSING** — `Event` conflates publication artifact with real-world occurrence. It has `source_url`, `fetched_at`, `content`, `extractor` — that is an article, not an event. |
| `Story` distinct from `Event` | **PARTIAL** — `Story` exists but is thin (title, summary, status, entity_ids, claim_ids, confidence). No `unresolved_question_count`, no `last_broadcast_at`, no association table. |
| `Correction` distinct from `Evidence` | **VIOLATED** — see below. |
| Claim status set | **PARTIAL** — `ClaimStatus` has only `UNVERIFIED`, `PARTIALLY_VERIFIED`, `VERIFIED`, `CONTRADICTED`. Spec §5.6.2 requires also `CORROBORATED`, `UNRESOLVED`, `SUPERSEDED`, `WITHDRAWN` (and `SUPPORTED`/`UNASSESSED` naming). |
| Claim types | **MISSING** — no `claim_type` field. Spec §5.6.1 requires OBSERVATIONAL, ATTRIBUTED, QUANTITATIVE, CAUSAL, PREDICTIVE, NORMATIVE, INTERPRETIVE. |
| Evidence relationship types | **MISSING** — `Evidence` has a single `claim_id`; no `ClaimEvidence` association with `supports`/`contradicts`/`qualifies`/`attributes`/`provides_context`/`leaves_unresolved`. |
| Three time axes | **PARTIAL** — `first_seen`/`last_updated` exist on Story; `published_at` vs `first_seen_at` distinction not consistently implemented. |
| Event status set | **PARTIAL** — no `status` field on `Event` at all. |
| Entity status | **PARTIAL** — `Entity` has no `status`. |
| `WorldSnapshot` completeness | **VIOLATED** — see §5. |
| CKU | **MISSING** (acceptable — spec §8.3 says interface-over-domain-objects; defer). |
| `Persona`, `Disagreement`, `BroadcastScript`, `BroadcastSession`, `ListenerMessage`, `VoiceProfile` | **MISSING** (expected — later phases). |

### The Correction → Evidence violation

`core/persistence.py:515`:

```python
async def correct_record(self, record_id: str, correction: Correction) -> str:
    correction_evidence = Evidence(
        claim_id=record_id,
        source_url="",  # Corrections are system-generated
        content=correction.explanation,
        extraction_method="correction",
        extractor="system",
        reliability=correction.confidence,
        epistemic_category=EpistemicCategory.ANALYSIS,
        correction_of=correction.corrects_id or record_id
    )
    evidence_id = await self.add_evidence(correction_evidence)
    return evidence_id
```

A correction is stored as an `Evidence` row with `extraction_method="correction"`.
This directly violates spec §5.8 ("A Correction is a first-class domain object.
It must not be represented merely as a new evidence record") and collapses the
distinction between "evidence supporting a proposition" and "a record that
something requires revision" (invariant I3).

## 5. Snapshot audit

`core/snapshot.py` (52 lines) is a thin pass-through to `WorldStore`.
`WorldStore.create_snapshot()`:

```python
snapshot = WorldSnapshot(
    story_count=len(stories),
    claim_count=len(claims),
    evidence_count=evidence_count,
    entity_count=len(entities),
    story_summaries={s.story_id: s.title for s in stories},
    ...
)
```

**This is an aggregation, not a snapshot.** Spec §5.9 requires a versioned
representation of the world state — what the system had observed, processed, and
retained. Missing:

| Required field | Present? |
|---|---|
| `knowledge_watermark` | No |
| `content_hash` | No |
| `included_record_manifest` | No |
| `unresolved_issue_count` | No |
| `snapshot_type` | No |
| `parent_snapshot_id` | No |
| Full serialized world state | No — only counts + `{id: title}` dicts |

Consequence: **the system cannot reconstruct what it knew at a particular time.**
Two snapshots can be compared by counts, but the underlying state is not
preserved. This fails Gate 4 and blocks Gate 5's "reproducible selection."

Also missing: atomic snapshot boundary (spec §5.9.1 requires a read transaction
with an explicit snapshot boundary so incompatible states are not combined).

## 6. Append-only audit

`test_story_cannot_be_updated_directly` **fails** — meaning append-only
enforcement is either not implemented or not working. Requires investigation in
Phase 1.

## 7. Documentation inventory (prior repo)

42 markdown files under `docs/`, including decision logs (9 decisions), domain
model, epistemic model, contracts, persistence, testing, and phase plans. These
are **design documents for a much larger system than the code implements** —
the gap between documented intent and 1,078 lines of code is the core finding
of this audit.

Key prior decisions (to be re-ratified in this repo's DECISIONS.md):

1. Local-first architecture
2. SQLite for persistence
3. Append-only world state
4. World state / persona opinion separation
5. YAML source configuration
6. CLI-first with web UI
7. Edge-TTS as default voice provider
8. Semantic embeddings over TF-IDF
9. Single-file → modular architecture

These are consistent with the published spec and should carry forward.

## 8. Architecture readiness

| Spec subsystem | LOC | Status |
|---|---|---|
| Persistence | 730 | PARTIAL — schema gaps, FK/uniqueness gaps |
| Domain models | 182 | PARTIAL — see §4 |
| Snapshots | 52 | VIOLATED — aggregation, not snapshot |
| Contracts | 114 | Not assessed in depth |
| Ingestion | 0 | MISSING |
| Knowledge compiler | 0 | MISSING |
| Editorial planner | 0 | MISSING |
| Generation / LLM provider | 0 | MISSING |
| Personas | 0 | MISSING |
| Validation | 0 | MISSING |
| Broadcast director | 0 | MISSING |
| TTS | 0 | MISSING |
| Audio | 0 | MISSING |
| Audience | 0 | MISSING |
| API | 0 | MISSING |
| UI | 0 | MISSING |
| Observability | 0 | MISSING |

## 9. Prioritized remediation plan

Ordered per spec §26.1. Full task breakdown in `PHASE_1_PLAN.md`.

| # | Task | Why | Gate |
|---|---|---|---|
| 1 | Fix async fixture misuse → 19 failures resolved | Blocks every domain test | 1 |
| 2 | Investigate 8 assertion count failures | Real logic or isolation bugs | 1 |
| 3 | Add `Source` model + table; migrate `source_url` to FK | Spec §5.2; prevents repeating the URL-as-entity mistake | 1 |
| 4 | Add `Article` model + table; split from `Event` | Spec §5.3, §5.5; article ≠ event ≠ story | 1 |
| 5 | Add `Correction` table; remove the Evidence shortcut | Spec §5.8; invariant I3 | 1 |
| 6 | Add `ClaimEvidence` association + relationship types | Spec §5.7; one passage ↔ several claims | 1 |
| 7 | Extend claim/event status enums to spec sets | Spec §5.5, §5.6.2 | 1 |
| 8 | Add `claim_type` enum | Spec §5.6.1; drives validation choice | 1 |
| 9 | Enforce FKs on every connection; add UNIQUE constraints | Spec §6.1 | 1 |
| 10 | Add migration system with versioned migrations | Spec §5.1, §6.1 | 1 |
| 11 | Verify append-only enforcement (currently failing) | Spec §3.3 | 1 |
| 12 | Rebuild `WorldSnapshot` as full state + watermark + hash + manifest | Spec §5.9; Gate 4 | 1 |
| 13 | Add transaction, recovery, and cross-process persistence tests | Spec §6.7, §22.3 | 1 |

## 10. Reproducing this baseline

```bash
cd ~/Projects/.mindbroadcast-reference
python3 -m pip install -r requirements.txt
python3 -m pytest -q
# expected: 27 failed, 49 passed
```

The reference checkout is read-only for this audit. **Phase 1 work happens in
this repository (`MindBroadCast02`)**, either by porting the models/schema with
fixes or by building fresh against the documented contracts. That choice is
recorded as a decision in DECISIONS.md (ADR-002).
