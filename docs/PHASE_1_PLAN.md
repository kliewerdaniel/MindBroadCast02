# Phase 1 Plan — Persistence and Domain Integrity

**Objective:** Make the information model trustworthy. (Spec §25 Phase 1, §26.1)
**Gate 1:** Persistence and migrations are reliable — the system can reliably
persist and retrieve its core domain records without violating the defined
relationships.

**Baseline:** 49 passed, 27 failed (see PHASE_0_AUDIT.md §2).

**Definition of done for this phase:** full suite green, all six new domain
objects persisted with FK enforcement, migration system in place, recovery tests
passing. Not "most tests pass."

---

## Work stream A — Test infrastructure (unblocks everything)

### A1. Fix the async fixture misuse

**Problem:** `world_store` is declared `async def` in conftest.py, so tests
receive an async generator object instead of a store. **19 of 27 failures.**

**Change:**

```python
# BEFORE
@pytest.fixture
async def world_store():
    store = WorldStore(db_path=":memory:")
    yield store

# AFTER
@pytest.fixture
def world_store():
    store = WorldStore(db_path=":memory:")
    store.connect()
    yield store
    store.close()
```

Remove the custom function-scoped `event_loop` fixture; `asyncio_mode = auto`
in pytest.ini handles loop management.

**Verify:** `python3 -m pytest -q` — expect ~19 fewer failures.

**Risk:** if `WorldStore.__init__` or `connect()` is itself async, adapt
accordingly — read the constructor before changing.

### A2. Investigate the 8 assertion failures

Tests with count mismatches: `test_list_stories` (`assert 4 == 2`),
`test_list_stories_with_filter` (`assert 3 == 2`), `test_list_claims`
(`assert 4 == 2`), `test_list_entities` (`assert 4 == 2`),
`test_list_snapshots` (`assert 2 == 1`), plus 3 more in the same classes.

Steps:
1. Re-run each in isolation after A1.
2. If still failing, read the test's fixture setup and the query.
3. Determine whether the **test expectation** or the **query** is wrong.
4. Fix the wrong one — do not weaken the assertion to make it pass.

**Verify:** each passes in isolation AND in the full suite.

### A3. Fix fixture isolation

Symptom to guard against: tests passing alone but failing in the full suite.
Each test gets a fresh in-memory DB. Confirm no module-level state leaks.

---

## Work stream B — Schema and domain objects

Order matters: `Source` first, because `Article` depends on it, and building
ingestion without `Source` repeats the URL-as-entity mistake (spec §5.2).

### B1. `Source` model + table

New `core/sources.py` (or extend models.py). Fields per DOMAIN_MODEL.md §Source:
`id`, `name`, `canonical_domain`, `source_type` (enum: RSS/ATOM/API/
OFFICIAL_DOCUMENT/WEB_PAGE/OTHER), `endpoint`, `publisher_identity`,
`ownership_group_id` (FK→ownership_groups, nullable), `trust_profile_id`
(FK, nullable), `enabled`, `poll_interval_seconds`, `last_attempt_at`,
`last_success_at`, `failure_count`, `created_at`, `updated_at`.

Add table `ownership_groups` (`id`, `name`, `created_at`) so ten publications
under one parent are not treated as ten independent confirmations.

**Tests:** create/retrieve/update source; ownership group relationship; a
disabled source is not polled.

### B2. `Article` model + table

Fields per DOMAIN_MODEL.md §Article. Critical: `source_id` FK (not `source_url`),
`published_at` vs `first_seen_at` as **separate** columns, `content_hash` UNIQUE,
`supersedes_article_id` self-FK, `publication_version`.

**Migration:** existing `events` rows carry `source_url`. Migration must create
a placeholder Source per distinct domain, then Article rows from event rows,
preserving all timestamps. **Record the migration in the migration log** — this
is a data-affecting migration and needs a test.

**Tests:** article persists with source FK; `published_at` and `first_seen_at`
remain distinct; a material revision creates a new version linked by
`supersedes_article_id`; the original version is not erased.

### B3. Split `Event` from `Article`

`Event` becomes a real-world occurrence: `id`, `canonical_title`, `event_type`,
`summary`, `status` (enum per STATE_MACHINES.md §6), `occurred_at_start`,
`occurred_at_end`, `first_observed_at`, `last_material_update_at`,
`parent_event_id`, `supersedes_event_id`, `confidence_state`, `metadata_json`.

Drop the article-artifact fields from `Event` (`source_url`, `fetched_at`,
`content`, `extractor`) — those move to `Article`.

**Tests:** an event with no article; an article referencing an event; multiple
articles per event; one article referencing several events.

### B4. `Correction` model + table — remove the Evidence shortcut

New table per DOMAIN_MODEL.md §Correction, with `correction_type` enum
(publisher / system-extraction / entity-resolution / event-association /
claim-assessment / broadcast / administrative), `target_type`, `target_id`,
`originating_source_id`, `original_value_reference`,
`corrected_value_reference`, `reason`, `detected_at`, `effective_at`,
`review_status`, `supersedes_correction_id`.

**Rewrite `correct_record()`** to insert into `corrections` and emit a
`CorrectionDetected` domain event. It must **not** call `add_evidence()`.

**Tests:**
- A correction creates a corrections row, not an evidence row.
- The original record is unmodified after correction.
- A publisher correction is distinguishable from a system-extraction correction.
- A correction on a broadcast-referenced claim links to the affected segment.
- The original transcript and audio remain historically identifiable.

This directly closes invariant I3 and invariant I4.

### B5. `ClaimEvidence` association

New table: `claim_id`, `evidence_id`, `relationship_type` (supports / contradicts
/ qualifies / attributes / provides_context / leaves_unresolved), `assessment`,
`assessment_method`, `assessed_at`, `metadata_json`. UNIQUE(claim_id,
evidence_id, relationship_type).

**Tests:** one evidence supports claim A while contradicting claim B; one
article yields several evidence rows; relationship types are enforced.

### B6. Extend status enums

`ClaimStatus` → `UNASSESSED`, `SUPPORTED`, `CORROBORATED`, `DISPUTED`,
`CONTRADICTED`, `UNRESOLVED`, `SUPERSEDED`, `WITHDRAWN` (spec §5.6.2).
`Event` gains `status` → `EMERGING`, `ACTIVE`, `DEVELOPING`, `DORMANT`,
`CONCLUDED`, `DISPUTED`, `MERGED`, `SUPERSEDED`.

Add `Claim.claim_type` (enum per spec §5.6.1).

**Tests:** transition table per STATE_MACHINES.md §5; invalid transitions
rejected; `CORROBORATED` requires distinct sources (test with two articles
sharing one `ownership_group_id` → must NOT corroborate).

That last test is the operational form of invariant I1 and is worth writing
carefully.

### B7. Foreign keys and uniqueness

- Enable `PRAGMA foreign_keys = ON` on **every** connection (centralize in the
  connection factory).
- `PRAGMA busy_timeout = 5000`.
- `PRAGMA journal_mode = WAL` for on-disk DBs.
- UNIQUE constraints: `articles.content_hash`, `sources.name`,
  `entities(canonical_name, entity_type)`, `claim_evidence(claim_id,
  evidence_id, relationship_type)`.

**Tests:** inserting an article with an orphan `source_id` fails; inserting a
duplicate content hash fails; FK violation on delete is rejected.

---

## Work stream C — Migrations

### C1. Migration framework

Versioned migrations, applied in order, tracked in a `schema_migrations` table
(upgrade from the existing `schema_version` table if it serves this purpose —
read it first).

```text
migrations/
├── 001_initial.py
├── 002_add_sources.py
├── 003_add_articles.py
├── 004_split_events.py
├── 005_add_corrections.py
├── 006_add_claim_evidence.py
└── 007_extend_enums.py
```

Each migration: `up()` and `down()`, idempotent, tested.

**Tests:** migrate up from empty; migrate up through the full chain; each
migration is idempotent; `down()` restores prior state.

### C2. Data migration from the legacy schema

The B2/B4 changes are data-affecting. Write a migration that:

1. Creates `sources` rows from distinct `source_url` domains.
2. Creates `articles` rows from `events` rows, preserving every timestamp.
3. Creates `events` rows for the genuine occurrences.
4. Moves correction-shaped evidence rows into `corrections`.
5. Records everything in the migration log.

**Test:** run the migration against a fixture legacy DB; assert no data loss and
that every timestamp survived with its meaning intact.

---

## Work stream D — Snapshots

### D1. Rebuild `WorldSnapshot` as real state

Replace the counts-and-titles aggregation with: `knowledge_watermark`,
`content_hash`, `included_record_manifest` (list of record IDs by type),
`unresolved_issue_count`, `snapshot_type`, `parent_snapshot_id`, plus a
canonical serialized representation of the world state.

Small deployment: full serialization + manifest. The serialization is stored as
an artifact with a DB reference (per PERSISTENCE_AND_EVENTS.md artifact
lifecycle — temp file, hash, move, then commit the DB record).

### D2. Atomic snapshot boundary

Read transaction + explicit boundary. Do not combine incompatible states because
different tables were read at different moments while updates continued.

**Tests:**
- Snapshot captures full state; reconstructing from the snapshot reproduces it.
- Two concurrent writers during snapshot creation do not produce a torn snapshot.
- A Thursday snapshot does not imply Monday knowledge (valid/observation/
  transaction times remain distinct).
- `knowledge_watermark` correctly identifies the latest incorporated event.

This closes Gate 4.

---

## Work stream E — Events, outbox, recovery

### E1. Domain event log

New table `domain_events` with the full envelope (event_id, event_type,
aggregate_type, aggregate_id, sequence_number, occurred_at, recorded_at,
causation_id, correlation_id, actor_type, actor_id, schema_version, payload).
Append-only.

Emit events from every write path: `ArticleCaptured`, `ClaimExtracted`,
`EvidenceLinked`, `ClaimAssessmentChanged`, `CorrectionDetected`,
`WorldSnapshotCreated`.

**Tests:** every commit emits exactly one event; the log is append-only
(attempts to update/delete are rejected); `occurred_at` ≠ `recorded_at`.

### E2. Transactional outbox

New table `outbox_messages`. Within one transaction: insert domain record +
insert domain event + insert outbox message; commit atomically. Dispatcher reads
pending, publishes, marks acknowledged. Tolerates duplicate delivery.

**Tests:** crash between commit and publish → message still delivered after
restart; duplicate delivery does not create duplicate active records.

### E3. Idempotency keys

Derive from operation type + canonical input ID + input version + config hash.
Enforce uniqueness on the key.

**Tests:** retrying claim extraction with the same article version and
extractor version does not create a second active claim set; a new extractor
version creates a new processing version instead.

### E4. Recovery procedure

Implement the 10-step startup recovery from PERSISTENCE_AND_EVENTS.md §Recovery.

**Tests (fault injection, spec §22.5):**
- Kill during a transaction → no partial records appear complete.
- Kill with pending outbox messages → delivered after restart.
- Kill with an in-flight snapshot → snapshot is either complete or absent, never torn.
- Missing audio/artifact reference → detected, never reported as playable.
- Worker restart with an outstanding lease → lease requeued where safe.

### E5. Append-only verification

`test_story_cannot_be_updated_directly` currently **fails**. Implement or repair
append-only enforcement so historical records cannot be silently rewritten.

**Tests:** direct update of a historical record is rejected; superseding creates
a new record linked by `supersedes_*`; the original remains queryable.

---

## Exit criteria — Gate 1

All of the following must be true and **demonstrated by a real test run**:

- [ ] Full suite green (0 failures). Baseline was 27 failed / 49 passed.
- [ ] `Source`, `Article`, `Event`, `Story`, `Claim`, `Evidence`, `Correction`,
      `Entity`, `WorldSnapshot` all persist and retrieve correctly.
- [ ] `Article` / `Event` / `Story` are distinct objects with distinct tables.
- [ ] `Correction` is a first-class record; `correct_record()` writes no
      Evidence rows.
- [ ] Foreign keys enforced on every connection; uniqueness constraints active.
- [ ] Versioned migration system; full chain applies from empty.
- [ ] Legacy data migration preserves all timestamps and relationships.
- [ ] `WorldSnapshot` captures full state with watermark, hash, manifest, and an
      atomic boundary.
- [ ] Domain event log append-only; every write path emits an event.
- [ ] Transactional outbox survives crash between commit and publish.
- [ ] Idempotency keys prevent duplicate active records.
- [ ] Recovery tests pass for every fault-injection case in work stream E4.
- [ ] Append-only enforcement verified by passing tests.
- [ ] Cross-process on-disk persistence verified (not just in-memory).
- [ ] Transaction boundaries documented in code (docstrings or a contracts doc).

## What is explicitly NOT in Phase 1

Ingestion, knowledge compilation, LLM providers, TTS, audio, personas,
validation, API, UI, editorial ranking. Those are Phases 2–7. **Do not build
them here** — a broadcasting interface cannot compensate for a database that
loses corrections.

## Suggested commit sequence

1. `fix: async fixture isolation in test suite` (A1)
2. `fix: list/filter query count assertions` (A2)
3. `feat: Source and ownership_group models with migration` (B1)
4. `feat: Article model and source FK migration` (B2)
5. `refactor: split Event from Article` (B3)
6. `feat: first-class Correction model, remove Evidence shortcut` (B4)
7. `feat: ClaimEvidence association and relationship types` (B5)
8. `feat: extend claim/event status enums and claim types` (B6)
9. `feat: FK enforcement and uniqueness constraints` (B7)
10. `feat: migration framework with up/down and tests` (C1)
11. `feat: legacy schema data migration` (C2)
12. `feat: WorldSnapshot full state with atomic boundary` (D1, D2)
13. `feat: domain event log and transactional outbox` (E1, E2)
14. `feat: idempotency keys` (E3)
15. `feat: startup recovery procedure` (E4)
16. `fix: append-only enforcement` (E5)

Each commit runs the full suite. A commit that leaves the suite red is not
complete.
