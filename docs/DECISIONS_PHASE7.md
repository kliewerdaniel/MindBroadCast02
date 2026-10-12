# ADR Addendum 7 — Decisions Taken During Phase 7 Implementation

Companion to `DECISIONS_PHASE1.md` through `DECISIONS_PHASE6.md`.

---

## ADR-043: The API layer defines its own response types, not the domain dataclasses

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §19.5: "Internal domain models should not automatically become
public API schemas. The API layer should expose stable representations that can
evolve independently of database implementation details."

**Decision:** `core/api/contracts.py` defines `StorySummary`, `ClaimSummary`,
`EvidenceSummary`, `SourceSummary`, `SegmentSummary`, `SessionSummary`,
`SystemStatus` — the API's own types. The internal `Story`/`Claim`/`Evidence`
dataclasses are never serialized directly. Every response envelope carries
`api_version`.

**Rationale:** a database column rename must not change the API contract. Coupling
the wire format to the ORM makes every schema migration a breaking change for
clients, which is exactly what §19.5 forbids.

---

## ADR-044: Listener input enqueues; it has no write path to the world model

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §17.5: "A listener request may create a research task. It must not
directly establish a claim in the world model." §17.1: a message "should not
synchronously block article ingestion, knowledge compilation, or audio delivery."

**Decision:** `MessagePipeline` produces exactly two outputs: a durable
`ListenerMessage` and, when eligible, a `ResearchTask` in a bounded queue. There
is no method that writes a claim, evidence, or story. The absence is structural,
not a policy check that could be bypassed.

**Rationale:** the enforcement is the shape of the class. A `ResearchTask` has no
`epistemic_status` field because it is not a claim — it is a proposal the planner
evaluates. Tested directly: `hasattr(pipeline, "claims")` is false.

---

## ADR-045: SHOW ANALYSIS has no chain-of-thought field, and redacts injected reasoning

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §18.4: "The system should not present private chain-of-thought
reasoning as an explanation. Instead, it should expose the evidence, structured
decision factors, recorded assumptions, validation outcomes, and concise
rationales needed to audit the result."

**Decision:** `ShowAnalysis` has no reasoning field. `FORBIDDEN_KEYS` lists the
reasoning variants (`chain_of_thought`, `reasoning`, `thinking`, `scratchpad`,
`raw_prompt`, `system_prompt`, …), and `ShowAnalysis.redact()` recursively strips
them from every nested payload before the view is built.

**Rationale:** the first guarantee (no field) is by construction; the redaction is
belt-and-braces for a payload that arrives with reasoning attached from elsewhere.
Both are tested — one asserts the field is absent, the other asserts an injected
`chain_of_thought` key is stripped.

---

## ADR-046: Transcript entries are created at delivery, not at generation

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §18.3: "The interface should distinguish generated text from text
actually delivered to the audio stream. A script that was generated but never
played should not appear indistinguishably in the public transcript."

**Decision:** `record_generated` creates entries with `delivered=False` and
`delivery_state=NOT_DELIVERED`. `mark_delivered` flips them to public. The public
transcript filter excludes anything not delivered. An interrupted segment is
marked `INTERRUPTED`, never silently `COMPLETED`.

**Rationale:** if entries were created at generation, a script that failed
validation or was never played would appear in the transcript as though spoken —
the exact indistinguishability §18.3 forbids. Tested: a generated-but-unplayed
script yields an empty public transcript and a full internal transcript.

---

## ADR-047: Operator controls are classified by risk: immediate, policy, operational

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §18.6: "Actions that affect persistent policy or data must have
explicit confirmation and audit records where appropriate." But muting playback
and disabling a source are different risk classes.

**Decision:** three `ControlKind`s. `IMMEDIATE` (mute, skip, pause) executes
without confirmation. `POLICY` (persona config, source toggle, editorial
priority, operating mode) raises `ConfirmationRequired` unless `confirmed=True`,
and audits the unconfirmed attempt too. `OPERATIONAL` (start, stop, refresh,
export) audits without gating.

**Rationale:** treating every control the same either annoys (confirming a mute)
or under-protects (disabling a source without confirmation). The unconfirmed
attempt is audited so a refused policy change is still visible in the trail.

---

## ADR-048: Abuse markers match word stems, not fixed phrases

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §17.5 requires catching "attempts to induce the system to repeat
false claims" and flagging "requests involving serious allegations." A
fixed-phrase list silently misses inflections.

**Bug found and fixed:** `SERIOUS_ALLEGATION_MARKERS` contained "embezzled" but
not "embezzle", so "Did the minister embezzle money?" was not flagged. The
symptom is silent — an unflagged serious allegation reads as a clean message.

**Decision:** markers are stems (`embezzl`, `conspir`, `fabricat`), matched with
`startswith` against the message's words. Multi-word phrases ("covered up") match
as substrings.

**Accepted limitation, recorded:** this is a pattern floor, not semantic
detection. Sophisticated rephrasing outside the marker set is missed. §17.5's
coordination detection is likewise heuristic.

---

## ADR-049: Feedback signals are confidence-capped and explicitly noisy

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §17.4: signals "should not be interpreted as direct measures of
truth, importance, or public consensus. A replay may indicate confusion rather
than interest. A skipped segment may reflect timing or technical problems rather
than dislike of its content."

**Decision:** `FeedbackCollector.preference_signal()` returns a bounded
preference reading with `confidence` capped at 0.5 and `noisy=True` always set.
`ambiguity_notes()` returns the spec's own caveats for the ambiguous signal types
present. There is no method that returns a truth, importance, or consensus score.

**Rationale:** the cap and the flag are the enforcement. A signal that could reach
1.0 confidence would let audience preference masquerade as established fact, the
popularity feedback loop §10.7 forbids at the editorial layer and §17.4 forbids at
the audience layer.
