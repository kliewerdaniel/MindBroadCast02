# Domain Model

Source: spec §5, §8.3, §10.8, §11.2, §12, §13, §15, §16, §17.

This is the **target** model. Implementation status per entity is tracked in
PHASE_0_AUDIT.md and IMPLEMENTATION_MATRIX.md.

## Record conventions (§5.1)

Every persistent domain record uses a stable ID and explicit timestamps:

```python
class RecordMetadata:
    id: str
    created_at: datetime
    updated_at: datetime
    schema_version: int
```

Rules:

- IDs stable across restarts.
- Foreign keys enforce valid relationships where supported.
- All stored timestamps timezone-aware, normalized to UTC.
- **Source-provided timestamps remain distinguishable from system-observation timestamps.**
- Mutable records have an explicit update policy.
- Historical observations must not be silently rewritten.
- Schema migrations versioned and tested.
- Externally supplied content retains provenance.
- Data models independent of any particular LLM provider.

## Three time axes (§5.9.1)

Never collapse these:

| Axis | Meaning | Example field |
|---|---|---|
| **Valid time** | When the proposition applied to the world. | `published_at`, `occurred_at_start` |
| **Observation time** | When the system first saw the information. | `first_seen_at`, `observed_at` |
| **Transaction time** | When it became durably recorded. | `created_at`, `recorded_at` |

A report published Monday, discovered Wednesday, processed Thursday must carry
all three. A Thursday snapshot must not imply the system knew it Monday.

## Source (§5.2)

An identifiable origin of information — publication, agency, feed, research
organization, press-release endpoint.

**Not the same as an article.** First-class entity.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID/stable string | Canonical source identifier. |
| `name` | string | Human-readable. |
| `canonical_domain` | string, nullable | Primary domain. |
| `source_type` | enum | RSS, Atom, API, official document, web page, other. |
| `endpoint` | string | Configured ingestion endpoint. |
| `publisher_identity` | string, nullable | Known publisher/org identifier. |
| `ownership_group_id` | UUID, nullable | Parent organization / ownership group. |
| `trust_profile_id` | UUID, nullable | Source-evaluation config reference. |
| `enabled` | bool | Whether ingestion is permitted. |
| `poll_interval_seconds` | int | Desired polling interval. |
| `last_attempt_at` | timestamp, nullable | Most recent fetch attempt. |
| `last_success_at` | timestamp, nullable | Most recent successful fetch. |
| `failure_count` | int | Tracked failures. |
| `created_at`, `updated_at` | timestamp | |

**`trust_profile_id` must not imply a single universal reliability score.**
Source evaluation is claim- and context-sensitive: a source may be dependable
for official statements and weak for independent interpretation; an agency is
authoritative about what it announced without being an independent authority on
the accuracy of its own claims.

**Ownership groups:** ten publications controlled by one organization are not
ten independent confirmations. The registry must support ownership relationships.

## Article (§5.3)

A specific captured publication or document. **A source artifact, not a verified
account of reality.**

| Field | Type | Notes |
|---|---|---|
| `id` | | |
| `source_id` | FK | |
| `canonical_url` | | |
| `original_url` | | |
| `title`, `author` | | |
| `published_at` | | Source-stated publication time (valid time). |
| `source_updated_at` | | |
| `first_seen_at` | | When the system observed it. |
| `fetched_at` | | |
| `language` | | |
| `content_hash` | | |
| `normalized_text` | | Suitable for search and extraction. |
| `raw_content_reference` | | Large HTML stored outside the DB, referenced here. |
| `extraction_status` | | |
| `publication_version` | | |
| `supersedes_article_id` | FK, nullable | Version chain. |
| `metadata_json` | | |
| `schema_version` | | |

`first_seen_at` and `published_at` are **deliberately separate**: a story
published three hours ago but discovered now is not equivalent to one first
observed three hours ago. This matters for freshness, outage recovery, and
historical reconstruction.

Content retention respects configured storage limits and source-access
restrictions; full republication of fetched content is not assumed.

### Article versioning (§5.3.1)

When a publisher materially changes an article, create a new captured version
linked to the prior one (separate version records or distinct article records
connected via `supersedes_article_id`).

> **A material source revision must not erase the historical version used to
> produce an earlier broadcast.**

Distinguish an ordinary metadata refresh from a substantive content correction.

## Entity (§5.4)

A recognized real-world or conceptual subject: people, organizations,
countries, institutions, locations, products, laws, conflicts, recurring
concepts.

| Field | Notes |
|---|---|
| `id` | |
| `canonical_name` | |
| `entity_type` | PERSON, ORGANIZATION, LOCATION, CONCEPT, … |
| `description` | |
| `aliases` | |
| `external_identifiers` | |
| `canonical_uri` | |
| `first_observed_at`, `last_observed_at` | |
| `status` | |
| `metadata_json` | |

**Resolution** combines: exact aliases, canonical identifiers, contextual
similarity, entity type, relationships to resolved entities, temporal context,
explicit source references.

Embedding similarity may generate candidate matches but **must never
independently authorize merges**. Two people with the same name must not be
merged because names and contexts look similar. Merges are recorded as explicit,
**reversible** operations.

## Event (§5.5)

A real-world occurrence or developing situation that many articles may describe.
**Not merely an article cluster.** Multiple articles can discuss one event; one
article can discuss several; a continuing story contains a series of related
events without all being identical.

| Field | Notes |
|---|---|
| `id` | |
| `canonical_title` | |
| `event_type` | |
| `summary` | |
| `status` | see below |
| `occurred_at_start`, `occurred_at_end` | Valid time. |
| `first_observed_at` | |
| `last_material_update_at` | |
| `parent_event_id` | FK, nullable |
| `supersedes_event_id` | FK, nullable |
| `confidence_state` | |
| `metadata_json` | |

**Event status:** `EMERGING`, `ACTIVE`, `DEVELOPING`, `DORMANT`, `CONCLUDED`,
`DISPUTED`, `MERGED`, `SUPERSEDED`.

These describe the **system's representation** of the event, not an objective
universal state. An event can be concluded by one source and ongoing by another
— represent the disagreement rather than silently resolving it.

## Story (§5.5.1)

The editorial grouping through which MindBroadcast tracks a subject over time.
An event is a modeled occurrence; a **story may encompass multiple related
events, articles, claims, and questions**.

Example legislative story: initial policy proposal → committee hearings →
parliamentary vote → court challenge → implementation deadline → economic
consequences. The system should not create an unrelated editorial identity for
every article about the proposal.

| Field | Notes |
|---|---|
| `id` | |
| `canonical_title` | |
| `editorial_summary` | Versioned derived artifact (§27.2). |
| `status` | |
| `created_at` | |
| `last_material_update_at` | |
| `last_broadcast_at` | |
| `last_substantial_coverage_at` | |
| `audience_relevance_state` | |
| `unresolved_question_count` | |
| `metadata_json` | |

A many-to-many association connects stories to events, articles, claims, and
entities, carrying a **role or relevance classification** where useful.

**Story summaries (§27.2):** each active story has a current editorial summary,
a historical-context summary, and a record of unresolved questions. Generated
from the world model, invalidated when material evidence changes, and **never
treated as authoritative facts** — the underlying claims and evidence are
authoritative.

## Claim (§5.6)

A proposition that can be evaluated against evidence.

| Field | Notes |
|---|---|
| `id` | |
| `canonical_proposition` | Natural-language form. |
| `normalized_subject_id`, `normalized_predicate`, `normalized_object` | Optional triple form for graph queries / dedup. |
| `claim_type` | see below |
| `epistemic_status` | see below |
| `first_observed_at` | |
| `last_evaluated_at` | |
| `valid_time_start`, `valid_time_end` | |
| `supersedes_claim_id` | FK, nullable |
| `created_by_component` | Which extractor/model produced it. |
| `metadata_json` | |

Not all claims reduce reliably to triples. Complex propositions retain their
canonical natural-language representation plus structured qualifiers (location,
time, quantity, unit, conditions).

### Claim types (§5.6.1)

| Type | Meaning |
|---|---|
| `OBSERVATIONAL` | About an observed occurrence or state. |
| `ATTRIBUTED` | That a person/source made a particular statement. |
| `QUANTITATIVE` | Involves a number, measurement, or statistic. |
| `CAUSAL` | That one factor caused or contributed to another. |
| `PREDICTIVE` | About a possible future state. |
| `NORMATIVE` | What should happen. |
| `INTERPRETIVE` | Explains or characterizes the significance of other information. |

**A claim's type determines which validation procedures are appropriate.** A
directly attributable quotation, a statistical estimate, and a causal hypothesis
must not be assessed by the same simplistic verification rule.

### Claim status (§5.6.2)

```
UNASSESSED, SUPPORTED, CORROBORATED, DISPUTED,
CONTRADICTED, UNRESOLVED, SUPERSEDED, WITHDRAWN
```

Explicit transition rules required. Semantics:

- `SUPPORTED` — available evidence supports the proposition under the system's
  defined evaluation criteria. **Not absolute certainty.**
- `CORROBORATED` — requires genuinely **distinct** supporting evidence, not
  repeated publication of the same original assertion.
- `CONTRADICTED` — available evidence conflicts materially with the proposition.
  Does **not** automatically mean every component of the proposition is false.
- `UNRESOLVED` — relevant evidence is currently unavailable.

## Evidence (§5.7)

A specific evidentiary observation relevant to a claim: an article passage, an
official document, a published statistical table, a direct quotation, a court
filing, a recorded public statement, a structured dataset, a source-provided
correction, an independently captured observation.

**Not synonymous with an article.** One article yields several pieces of
evidence; one passage is relevant to several claims; a document can support one
proposition while contradicting another.

| Field | Notes |
|---|---|
| `id` | |
| `evidence_type` | |
| `article_id` | FK, nullable |
| `source_id` | FK |
| `content_reference` | Source location. |
| `excerpt` | |
| `content_hash` | |
| `observed_at` | |
| `source_published_at` | |
| `extraction_method` | |
| `extraction_model`, `extraction_version` | |
| `provenance_status` | |
| `metadata_json` | |

Evidence-to-claim relationships stored separately:

```text
ClaimEvidence
├── claim_id
├── evidence_id
├── relationship_type   # supports | contradicts | qualifies | attributes | provides_context | leaves_unresolved
├── assessment          # calibrated score or categorical result — NOT a universal truth probability
├── assessment_method
├── assessed_at
└── metadata_json
```

**A generated summary without a traceable underlying passage is not primary
evidence.** Evidence records must preserve source location and extraction provenance.

## Correction (§5.8)

**A first-class domain object. It must not be represented merely as a new
evidence record.**

Evidence establishes or challenges propositions. A **correction records that a
prior representation, report, or statement requires revision.**

| Field | Notes |
|---|---|
| `id` | |
| `correction_type` | publisher / system-extraction / entity-resolution / event-association / claim-assessment / broadcast / administrative |
| `target_type`, `target_id` | What is being corrected. |
| `originating_source_id` | FK |
| `original_value_reference` | |
| `corrected_value_reference` | |
| `reason` | |
| `detected_at`, `effective_at` | |
| `review_status` | |
| `supersedes_correction_id` | FK, nullable |
| `metadata_json` | |

Distinguish **a publisher correcting an article** from **MindBroadcast
discovering its own extraction was wrong**. A publisher correction does not
prove every associated claim false, but it **requires reassessment** of claims
that depended on the corrected material. A revised model assessment must not be
mislabeled as a correction to the original source.

When a correction affects a previously broadcast claim: evaluate whether a
public correction is warranted. The original transcript and audio remain
historically identifiable, with the correction linked to the affected segment.

## WorldSnapshot (§5.9)

The system's best recorded view of a defined portion of the information
environment at a point in time. **Not an omniscient snapshot of the real world**
— a versioned representation of what the system had observed, processed, and retained.

| Field | Notes |
|---|---|
| `id` | |
| `created_at` | |
| `knowledge_watermark` | Latest durable event incorporated. |
| `schema_version` | |
| `snapshot_type` | |
| `parent_snapshot_id` | FK, nullable |
| `content_hash` | |
| `included_record_manifest` | |
| `unresolved_issue_count` | |
| `metadata_json` | |

Small deployment: canonical serialized representation + record manifest.
Larger: compact snapshot + reference to the immutable event sequence from which
it can be reconstructed.

**Snapshot creation must be atomic with respect to the logical state captured.**
Do not combine incompatible DB states because different tables were read at
different moments while updates continued. For SQLite: read transaction +
explicit snapshot boundary; separate expensive serialization from writes where
possible while preserving a consistent source state.

## Canonical Knowledge Unit — CKU (§8.3)

A stable, addressable representation of a meaningful piece of knowledge or an
assertion about the world. **Not synonymous with a sentence, an embedding, or a
source document.** One article → many CKUs; several articles → one CKU; a CKU
may be revised while historical versions remain accessible.

```text
CanonicalKnowledgeUnit
├── id
├── canonical_content
├── unit_type                  # claim-backed vs synthesized — the schema must identify which
├── epistemic_status
├── source_references
├── evidence_references
├── entity_references
├── event_references
├── relationship_references
├── validity_interval
├── observation_interval
├── version
├── supersedes_version
└── provenance_manifest
```

**Safest initial design: CKUs are a stable interface over existing domain
objects, not a second competing system of record.** A CKU can be assembled from
a claim + its evidence, or represent a higher-level synthesized proposition —
the schema must identify which type it is.

**CKU identity (§8.3.1)** does not depend exclusively on text. Two differently
worded propositions may express the same assertion; two nearly identical
sentences may refer to different times or quantities. Preserve the distinction
between: a stable conceptual identity, a particular textual representation, a
particular evidence-backed version, a particular assessment of that version.
Material change creates a new version or a distinct claim linked via explicit
supersession — never a silent rewrite.

## Provenance graph (§5.10)

```text
Source
  └── published
       └── Article Version
            └── contains
                 └── Evidence
                      └── supports
                           └── Claim
                                └── associated_with
                                     └── Event
                                          └── grouped_into
                                               └── Story
                                                    └── selected_for
                                                         └── Broadcast Segment
                                                              └── contains
                                                                   └── Spoken Claim
```

Also represents negative/qualified relationships: evidence that contradicts a
claim, a correction that supersedes a previous assessment.

Initially relational tables with foreign keys and indexed association tables;
SQLite supports recursive queries and many-to-many. A dedicated graph engine
only when query complexity or scale demonstrates real need.

## Broadcast-domain objects

### BroadcastPlan / PlannedSegment (§11.2)

```text
BroadcastPlan
├── id, session_id, created_at, plan_version
├── planning_horizon
├── world_snapshot_id
├── editorial_policy_version
├── status
└── segments[]

PlannedSegment
├── id, story_id
├── segment_type
├── assigned_personas
├── target_duration_seconds
├── required_context
├── required_claims
├── prohibited_claims
├── unresolved_questions
├── transition_from_previous
├── interruption_policy
└── status
```

The plan is a **proposal** that may be revised when material developments occur.
**Once a segment has begun generating audio, its already published portions
must not be retroactively rewritten.**

### BroadcastSession (§13.2)

```text
BroadcastSession
├── id
├── started_at, stopped_at
├── operating_mode
├── current_plan_id
├── current_segment_id
├── current_persona_id
├── playback_state
├── session_sequence
├── last_completed_segment_id
├── pending_interruption_id
└── metadata_json
```

Persisted often enough to support recovery after process termination. In-memory
buffers may be lost; the system must know which segments were generated,
queued, started, completed.

### BroadcastScript / ScriptTurn (§15.1)

```text
BroadcastScript
├── id
├── segment_id
├── version
├── created_at
├── generation_request_id
├── evidence_packet_id
├── status
└── turns[]

ScriptTurn
├── id
├── sequence
├── persona_id
├── text
├── speech_act
├── claim_references
├── evidence_references
├── uncertainty_markers
├── expected_duration
├── generation_metadata
└── validation_status
```

The script is a **first-class persistent artifact**, not an ephemeral string.

### Persona (§12.2)

```text
Persona
├── id
├── name
├── role
├── description
├── voice_profile_id
├── speaking_style
├── analytical_preferences
├── epistemic_preferences
├── rhetorical_preferences
├── interaction_policy
├── memory_policy
├── enabled
├── configuration_version
└── created_at
```

Identity and editorial instructions stored **separately from transient
generation prompts**. A persona update creates a versioned configuration change
rather than silently rewriting instructions used by a previously generated segment.

### Disagreement (§12.5)

```text
Disagreement
├── id
├── subject
├── proposition
├── persona_a_id, position_a
├── persona_b_id, position_b
├── shared_facts
├── disputed_assumptions
├── evidence_references
├── disagreement_type        # factual uncertainty | competing causal explanations | value judgments |
                             # forecasts | institutional-incentive interpretations |
                             # evidence-quality assessments | policy recommendations
└── resolution_status
```

### VoiceProfile (§16.3)

```text
VoiceProfile
├── id
├── provider_id
├── voice_id
├── language
├── default_rate
├── pitch_parameters
├── output_format
├── normalization_policy
└── configuration_version
```

A voice profile is **separate from a persona**. One persona may have different
voices for different modes; multiple personas may share a provider with
different voices. Unsupported parameters are rejected or explicitly ignored per
a documented policy.

### ListenerMessage (§17.2)

```text
ListenerMessage
├── id
├── session_id, client_id
├── received_at
├── message_type
├── content
├── topic_candidates
├── moderation_status
├── processing_status
├── response_segment_id
└── metadata_json
```

Data minimization: persistent listener identity optional; a local deployment may
use a generated session identifier with no accounts or external auth.

### Audio artifact (§16.7)

Artifact ID, segment ID, script version, TTS provider, voice profile version,
audio format, content hash, duration, creation timestamp, synthesis
configuration, retention policy. Retention is configurable — indefinite audio
storage consumes substantial disk. Transcripts and provenance may be retained
longer than large audio files when limits require that distinction.

## Known gaps vs. the current implementation

Tracked in PHASE_0_AUDIT.md:

- **No `Source` entity** — articles carry `source_url` directly.
- **No `Article` entity** — the closest object is `Event`, which conflates
  publication artifact with real-world occurrence.
- **`Correction` collapses into `Evidence`** — `correct_record()` writes an
  Evidence row, losing the correction chain.
- **`WorldSnapshot` stores counts + summary dicts**, not a complete immutable
  world representation, and has no `knowledge_watermark`, `content_hash`, or
  record manifest.
- Claim/evidence status enums are thinner than the target set (e.g.
  `ClaimStatus` has no `CORROBORATED`, `UNRESOLVED`, `SUPERSEDED`, `WITHDRAWN`).
- No CKU abstraction, no provenance-graph association table, no Persona,
  Disagreement, BroadcastScript, BroadcastSession, or ListenerMessage objects.
