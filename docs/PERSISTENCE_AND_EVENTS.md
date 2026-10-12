# Persistence, Events, and State Management

Source: spec §6.

## SQLite as system of record (§6.1)

SQLite is the default for the initial local deployment: transactional integrity,
foreign-key enforcement, mature migration tooling, straightforward operational
model, single-file backup and portability.

**Using SQLite does not automatically make an application reliable.** Required
practices:

| Practice | Requirement |
|---|---|
| Foreign keys | Enabled on **every** connection. |
| Transactions | Explicit boundaries. |
| Busy timeout | Configured (e.g. 5000 ms). |
| WAL mode | Where appropriate for concurrent readers/writers. |
| Long operations | Never hold a write transaction open during network or model operations. |
| Concurrent writers | Bounded. |
| Migrations | Used for all schema changes; versioned and tested. |
| Uniqueness | Enforced for canonical identities and dedup keys. |
| SQL | Parameterized always. |
| Retention/backup | Defined policies. |
| Recovery | Tested after interrupted writes and process termination. |

Connection creation and transaction handling are **centralized** — no module
establishes its own inconsistent configuration.

## Event log (§6.2)

An append-only log of significant domain events. Events describe **state
transitions**, not function-call mirrors.

```
SourceRegistered          ArticleObserved           ArticleCaptured
ArticleVersionDetected    ClaimExtracted            EvidenceLinked
ClaimAssessmentChanged    EntityResolved            EventAssociationChanged
CorrectionDetected        WorldSnapshotCreated      StoryPriorityChanged
StorySelected             SegmentPlanned            ScriptGenerated
ScriptValidated           BroadcastSegmentStarted   BroadcastSegmentCompleted
AudioChunkProduced        ListenerMessageReceived   CorrectionPublished
```

The log supports: debugging, recovery, auditability, reconstructing historical
editorial decisions, evaluating why a story was selected, determining which
evidence was available when a claim was broadcast, identifying the consequences
of a correction, comparing behavior across versions.

### Event envelope (§6.2.1)

```text
DomainEvent
├── event_id
├── event_type
├── aggregate_type
├── aggregate_id
├── sequence_number
├── occurred_at        # when the underlying action occurred, to the extent known
├── recorded_at        # when the event was durably accepted
├── causation_id       # the event/request that caused this one
├── correlation_id     # links a broader workflow, e.g. one broadcast segment
├── actor_type
├── actor_id
├── schema_version
└── payload
```

`occurred_at` ≠ `recorded_at`. Causation and correlation IDs are essential when
reconstructing asynchronous operations.

## Event sourcing without unnecessary complexity (§6.3)

A practical hybrid, not full event sourcing for every entity:

- Relational tables hold **current operational state**.
- Append-only domain events preserve **significant state transitions**.
- Immutable source captures preserve **historical inputs**.
- Versioned snapshots preserve **selected views** of the world model.
- Provenance relationships connect inputs, transformations, decisions, outputs.

Where an entity's historical evolution is especially important, preserve
explicit revisions or reconstructable transitions.

## Transactional outbox (§6.4)

**Problem:** update the DB, then notify a worker through an in-memory queue.
Crash between commit and publish → the work never happens. Reverse ordering →
a worker receives a notification for a state change that never commits.

**Solution:** transactional outbox.

```
Transaction:
    INSERT Article
    INSERT DomainEvent
    INSERT OutboxMessage
COMMIT

Dispatcher:
    Read pending OutboxMessage
    Publish to worker queue
    Mark delivery acknowledged
```

- The dispatcher **must tolerate duplicate delivery**.
- Workers **must be idempotent**.
- Design assumes **at-least-once delivery**, not exactly-once execution across
  arbitrary subsystems.

## Idempotency (§6.5)

Mandatory for: fetch retries, article capture, content extraction, claim
extraction, embedding generation, story assignment, segment scheduling, TTS
generation, audio publication, listener-message ingestion.

Idempotency key derives from: **operation type + canonical input identifier +
input version + processing configuration**.

```
claim-extraction:
    article_version_id
    extractor_version
    extraction_configuration_hash
```

A retry with the same input and configuration must not silently produce a second
active set of claims. If a newer extraction configuration produces different
results, record those as a **new processing version**.

## Database and filesystem consistency (§6.6)

Large artifacts (raw source captures, generated audio, diagnostic bundles) live
outside SQLite. A DB transaction cannot atomically commit a filesystem write.

### Controlled artifact lifecycle

```
1. Write the artifact to a temporary location.
2. Flush and finalize the file.
3. Calculate its content hash.
4. Move it into its durable artifact location.
5. Commit a database record referencing the finalized artifact.
6. Reconcile orphaned files or missing references during maintenance.
```

**A failed artifact commit must not result in a DB record claiming a complete,
playable file exists when it does not.**

Artifact cleanup must never delete files referenced by active or historical
broadcast records.

## Recovery and consistency checks (§6.7)

On startup, a bounded recovery procedure:

```
1.  Verify database accessibility.
2.  Apply permitted migrations.
3.  Check critical schema and foreign-key invariants.
4.  Recover abandoned job leases.
5.  Requeue expired in-progress work where safe.
6.  Reconcile incomplete artifact writes.
7.  Identify outbox messages that remain pending.
8.  Detect incomplete broadcast sessions.
9.  Restore the most recent valid operational state.
10. Record the recovery result.
```

Recovery must not require rebuilding the entire knowledge graph unless
corruption or a specific migration makes it necessary.

**The system distinguishes recoverable incomplete work from genuine
data-integrity violations, and must not silently repair serious inconsistencies
without leaving a record of what changed.**

## Worker leases

Background work uses leases so a crashed worker's in-progress job can be
recovered rather than lost forever. Tracked metrics: worker lease expirations,
recovery events. Expired leases are requeued where safe (recovery step 5).

## Transaction boundary rules

| Operation | Rule |
|---|---|
| LLM generation | Reads before, validation after, model call entirely **outside** the transaction. |
| TTS synthesis | Artifact finalized on disk before the DB record commits. |
| Network fetch | Response fully received and validated before the capture transaction. |
| Snapshot creation | Read transaction + explicit snapshot boundary. |
| Knowledge integration | All-or-nothing; partial processing must be explicitly flagged incomplete. |

## Migration policy (§24.5)

- All schema changes via versioned migrations, tested.
- Upgrades include: schema migration, config migration where necessary,
  compatibility checks, backup recommendations or automated backup, explicit
  handling of incompatible persisted artifacts, rollback/recovery guidance.
- **Model and prompt upgrades are separate from database schema upgrades.**
  Changing an LLM must not silently regenerate every stored claim or reinterpret
  every historical segment.

## Backup and restore (§24.4)

A backup includes: database state, schema version info, safe-to-retain
configuration, necessary provenance artifacts, a manifest of included files,
checksums where appropriate.

**Secrets are not automatically included in backups.**

The system periodically tests **restoration** — successful backup creation does
not guarantee recoverability.
