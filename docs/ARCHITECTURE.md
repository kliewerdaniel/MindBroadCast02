# Architecture

Source: spec §3, §4, §13, §14, §16, §19, §21, §27.

## Governing principles (normative, spec §3)

### P1. Local-first execution

Default deployment runs on the user's machine or an explicitly controlled
environment. Persistent state in local storage. LLM behind a replaceable
provider interface. TTS has a lightweight local implementation. The core
information model does not depend on a cloud vendor.

Consequences: a network outage must not destroy local state; a provider outage
must not corrupt the knowledge base; a model change must not force
reinterpretation of the historical database.

### P2. Deterministic boundaries around probabilistic components

LLMs are not reliable substitutes for transaction management, access control,
referential integrity, or exact state transitions.

- Model may propose an entity match → deterministic code decides whether to commit.
- Model may propose a claim → evidence pipeline records source and validation status.
- Model may propose a priority → scheduler applies explicit constraints.
- Model may generate a script → validator decides broadcast eligibility.
- Model may suggest a DB update → a controlled service performs the write.
- Model may recommend a persona → configuration change requires admin process.
- Model may flag a potential correction → workflow preserves the original record.

> **Models propose. Schemas constrain. Services authorize. Transactions commit.
> Evidence records what happened.**

### P3. Append-only provenance

Preserve: the original observation, subsequent versions/corrections, the
relationships between records, time observed, source-stated publication/update
time, processing history, and the evidence/decisions of any broadcast that used
the information.

Mutable operational state may exist for performance, but changes that materially
affect editorial decisions or published content must be **reconstructable from
durable records**. Append-only means important historical facts don't vanish —
not that every cache is immutable.

### P4. Explicit epistemic status

Every significant information object carries a status describing what the system
knows about it (see EPISTEMIC_INVARIANTS.md). Statuses are not a universal
numerical truth measure. Five articles repeating one report ≠ five independent
confirmations.

### P5. Separation of observation, interpretation, publication

Three distinct products:

1. **Information model** — what the sources report.
2. **Analytical model** — what the evidence may mean.
3. **Published broadcast** — what the system chooses to communicate.

These must not collapse into a single LLM conversation. A persona may interpret
evidence differently without altering evidence records. A broadcast may omit a
claim without deleting it. A correction may change a claim's status without
rewriting an old transcript to conceal what was said.

## Layered architecture (spec §4.1)

```
┌─────────────────────────────────────────────────────────────┐
│                     EXTERNAL WORLD                           │
│  RSS/Atom │ News APIs │ Official Sources │ Web │ Listeners  │
└─────┬─────────┬───────────┬───────────────┬──────────┬──────┘
      ▼         ▼           ▼               ▼          ▼
┌─────────────────────────────────────────────────────────────┐
│                    INGESTION LAYER                           │
│ Connectors → Fetch Queue → Parsing → Normalization          │
│                         → Deduplication → Source Registry    │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                 KNOWLEDGE COMPILER                           │
│ Article → Entity Extraction → Event Resolution              │
│         → Claim Extraction → Evidence Linking               │
│         → Contradiction Detection → Provenance Graph         │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                  PERSISTENT WORLD MODEL                      │
│ Sources │ Articles │ Events │ Claims │ Evidence │ Entities  │
│ Corrections │ Relationships │ Snapshots │ Historical State  │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                 EDITORIAL CONTROL PLANE                      │
│ Change Detection → Story Ranking → Coverage Planning        │
│                  → Topic Selection → Segment Assignment      │
│                  → Interruption and Transition Decisions     │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                PERSONA AND ANALYSIS LAYER                    │
│ Research Context → Persona Memory → Independent Analysis    │
│                  → Cross-Persona Discussion                  │
│                  → Script Construction                       │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                  BROADCAST VALIDATION                        │
│ Claim Verification → Provenance Check → Policy Check        │
│                    → Length/Timing → Script Approval         │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                     MEDIA PIPELINE                           │
│ Text → TTS → Audio Buffer → Stream Multiplexer              │
│                            ├── Browser Player                │
│                            ├── Local Audio Output            │
│                            └── Optional Radio Server         │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                  AUDIENCE AND FEEDBACK                       │
│ Playback State → Listener Interaction → Feedback Events     │
│                → Preference Updates → Editorial Influence    │
└──────────────────────────────┬──────────────────────────────┘
                               └──────► World Model / Planner
```

All major services emit **structured operational events** for observability,
recovery, historical reconstruction, and evaluation.

## Logical module boundaries (spec §4.2)

| Module | Responsibility | Phase |
|---|---|---|
| `persistence` | DB transactions, migrations, repositories, storage abstractions. | 1 |
| `world_model` | Persistent representation of current and historical information environment. | 1, 4 |
| `snapshots` | Versioned world-state representations at points in time. | 4 |
| `source_registry` | Source identity, config, reliability history, permissions. | 2 |
| `ingestion` | Source polling, fetching, parsing, normalized article creation. | 2 |
| `normalization` | Canonical URLs, timestamps, content normalization, fingerprints. | 2 |
| `deduplication` | Duplicate and near-duplicate detection. | 2 |
| `knowledge_compiler` | Extraction/integration of entities, claims, events, relationships. | 3 |
| `evidence` | Evidence records, support relationships, contradiction tracking, claim status. | 3 |
| `story_engine` | Story clustering, development detection, novelty, coverage state. | 4 |
| `editorial_planner` | Topic ranking, scheduling, transitions, interruptions. | 4–5 |
| `generation` | LLM provider abstraction and structured generation requests. | 5 |
| `personas` | Persona config, memory retrieval, perspective generation, dialogue. | 6 |
| `validation` | Claim-to-evidence checks, script validation, broadcast eligibility. | 5 |
| `broadcast` | Segment lifecycle, turn scheduling, queue management, session state. | 5 |
| `tts` | Speech synthesis, voice configuration, audio metadata. | 5 |
| `audio` | Buffering, sequencing, format conversion, delivery. | 5 |
| `audience` | Listener messages, playback events, preferences, feedback. | 7 |
| `api` | HTTP endpoints, streaming endpoints, administrative interfaces. | 7 |
| `ui` | Dashboard, transcript, playback controls, system inspection. | 7 |
| `observability` | Metrics, traces, structured logs, health checks, diagnostics. | 1+ |

These boundaries **control complexity and ownership — they are not a mandate to
create dozens of packages immediately.** A modular monolith is preferable until
there is demonstrated need for independent scaling, process isolation, or remote
execution (§4.1).

## Process architecture (spec §4.3)

- Async I/O for network, event delivery, API, audio streaming.
- CPU-intensive work (bulk embeddings) bounded separately so it cannot
  monopolize the event loop.
- LLM inference and TTS each have independent concurrency controls.
- **A model request must never hold a database transaction open.**
- Bound concurrent DB writers.

### Preferred processing pattern (§4.3)

```
1. Read context from persistent storage.
2. Construct an immutable generation request.
3. Execute the model request OUTSIDE the transaction.
4. Validate the result.
5. Commit the accepted result in a SHORT transaction.
6. Emit a durable event describing the state transition.
```

Same principle for network fetching and speech synthesis.

### Initial process topology (§4.3.1)

```
mindbroadcast/
├── application
│   ├── lifecycle          # startup/shutdown, recovery, mode transitions
│   ├── configuration      # hierarchical config, validation, fail-closed
│   └── dependency_container
├── ingestion_workers
├── knowledge_workers
├── editorial_worker
├── persona_generation_workers
├── validation_worker
├── tts_workers
├── audio_stream_worker
├── api_server
├── scheduler
└── maintenance_worker
```

Logical execution responsibilities, not necessarily separate OS processes.
First implementation: single application process with controlled background
tasks and bounded worker pools.

## Provider contracts

### LLM provider (spec §14.1)

```python
class LanguageModelProvider:
    async def generate(self, request): ...
    async def generate_structured(self, request, schema): ...
    async def stream(self, request): ...
    async def health_check(self): ...
    async def capabilities(self): ...   # must not claim a capability it can't deliver
```

Domain logic must not depend on provider-specific APIs. Capabilities must be
declared explicitly (streaming, structured output, tool use, context length).

### TTS provider (spec §16.2)

```python
class TTSProvider:
    async def synthesize(self, request): ...
    async def stream_synthesize(self, request): ...
    async def health_check(self): ...
    async def capabilities(self): ...
```

Request carries: segment ID, script-turn ID, text, voice profile, language,
speaking-rate preference, output format, synthesis config version, deadline.
Response: audio/chunks, duration metadata, format info, synthesis-attempt ID.

### Source adapter (spec §7.2)

```python
class SourceAdapter:
    async def fetch_updates(self, cursor=None): ...
    async def parse_response(self, response): ...
    async def health_check(self): ...
    async def get_checkpoint(self): ...
    async def restore_checkpoint(self, checkpoint): ...
```

All adapters must support explicit error reporting, checkpoint handling, and
normalized output. Never invent missing publication timestamps or stable IDs —
preserve the uncertainty.

## Model routing (spec §14.2, §23.5)

Route by task, not by habit. Do not use the most capable model for everything.

| Task | Preferred capability |
|---|---|
| Article classification | Fast, inexpensive inference. |
| Entity extraction | Reliable structured extraction. |
| Claim extraction | Strong instruction following + source grounding. |
| Contradiction analysis | Reasoning + explicit evidence comparison. |
| Story ranking assistance | Structured evaluation over a bounded feature set. |
| Persona dialogue | Natural language generation, stable stylistic control. |
| Long-form analysis | Larger context, stronger reasoning. |
| Script validation | Separate verifier prompt/model + deterministic checks. |
| Embedding generation | Dedicated local embedding model. |

Routing considers: task type, output schema, context length, latency budget,
available memory, quality requirement, privacy requirement, provider
availability, estimated cost. Resource-aware routing may defer non-urgent work
rather than silently lowering the quality of every operation.

## Storage layout (spec §24.1)

Source tree:

```
mindbroadcast/
├── app/
├── tests/
├── docs/
├── migrations/
├── config/
├── scripts/
├── pyproject.toml
├── README.md
└── .env.example
```

Runtime data (outside the source directory, paths configurable):

```
mindbroadcast-data/
├── database/
│   └── mindbroadcast.sqlite
├── artifacts/
│   ├── source_captures/
│   ├── audio/
│   └── exports/
├── logs/
├── cache/
└── backups/
```

## Observability (spec §21)

Metrics per subsystem — ingestion (fetch attempts/failures, articles
discovered/captured, duplicate rate, latency, oldest pending task, per-source
failure rate), knowledge compilation (documents awaiting processing, extraction
success, schema-validation failure rate, entity-resolution rate, unresolved
candidates, claim latency, evidence-linking success, contradiction candidates,
reprocessing backlog), editorial planning (eligible stories, ranking latency,
plan failures, time since last completed segment, coverage distribution,
repeated-content rate, breaking-update latency, unresolved high-priority
questions), language models (requests per task, latency distribution,
output-validation failures, timeouts, retries, token usage, memory, provider
availability), audio (TTS latency, time-to-first-audio, generation failures,
queue depth, buffer underruns, format errors, interruptions, time without
playable audio), system health (uptime, DB errors/size, artifact usage, outbox
backlog, lease expirations, recovery events, API latency, client disconnects).

**Health checks distinguish liveness from readiness.** Readiness identifies
degraded capabilities: the system may serve transcripts and inspect stories
while its TTS provider is down. A single global `healthy: false` obscures that.

Dashboard indicators show real state from subsystem health checks — never
manually maintained decorative lights.

## Security posture (spec §20, §7.6, §14.5)

- Bind loopback by default; remote access requires explicit config + auth + transport protection.
- External content (articles, listener messages) is **untrusted data, never instructions**.
- Source fetching restricted to permitted schemes/destinations; redirects checked against the same policy; SSRF/loopback/metadata endpoints blocked.
- Connection, response-size, redirect, and timeout limits.
- Strict instruction/data separation in prompts; no unrestricted tool execution; allowlisted tools; all proposed IDs validated against the DB; no secrets or DB credentials in prompts; deterministic authorization outside the model.
- Least privilege per subsystem: source adapters cannot modify personas; audio cannot alter claim assessments; listener input cannot modify trust profiles.
- Secrets outside the application DB and never in persona memory, prompt logs, transcript exports, public API responses, debug bundles, or client-facing errors.

## Graph vs relational (spec §27.1)

**Start with relational storage; expose a graph-oriented domain interface.**
The application needs graph semantics, not necessarily a graph database.
SQLite represents claims, entities, events, stories, evidence via relational
tables and association records; recursive queries and application-level
traversal cover most graph operations. Requirements: relationships explicit,
indexed, versioned where necessary, traceable. A dedicated graph index may be
introduced later as a **derived representation** — never a second source of truth.

## No global mutable world document (spec §27.3)

Maintain normalized domain records + durable event history + versioned
snapshots. The global world model is the logical combination of those
components. Individual tasks work against **bounded, versioned views**.
