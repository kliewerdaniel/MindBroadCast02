# ADR Addendum 8 — Decisions Taken During Phase 8 Implementation

Companion to `DECISIONS_PHASE1.md` through `DECISIONS_PHASE7.md`.

---

## ADR-050: Subsystem health is derived from metrics, never set by hand

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §21.5: "The dashboard should show real state from subsystem health
checks, not manually maintained decorative indicators."

**Decision:** `MetricsRegistry.health()` computes a subsystem's state from its
success/failure counters. There is no `set_health()` method. A subsystem with no
metrics reports `UNAVAILABLE`; a majority-failure rate reports `UNAVAILABLE`;
≥10% failure reports `DEGRADED`; otherwise `HEALTHY`.

**Rationale:** a manual health flag drifts from reality the moment a subsystem
degrades without anyone remembering to update it — which is precisely when the
dashboard is needed. Deriving from metrics makes the indicator incapable of
lying.

---

## ADR-051: Logs redact content; diagnosis uses identifiers

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §21.3: "a model-generation failure can usually be diagnosed using
the request ID, model, error category, and validation result without recording
the full listener message or source text in every log entry."

**Decision:** `RedactingFormatter` replaces `SENSITIVE_FIELDS`
(`content`, `source_text`, `excerpt`, `prompt`, `raw_prompt`, `system_prompt`,
`token`, `api_key`, …) with `[redacted]`, recursively through nested dicts.
Identifiers survive.

**Rationale:** listener messages and source articles are untrusted and often
private. Logging them in full turns every log file into a data store nobody
audited. The spec names the exact fields that suffice for diagnosis; everything
else is redacted.

---

## ADR-052: Idempotency keys make backpressure coalesce instead of duplicate

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §23.3 rule 7: "Avoid generating duplicate segments to compensate for
delayed work." A bounded queue under load tempts a retry loop into re-enqueuing
in-flight work.

**Decision:** `PriorityScheduler` tracks pending idempotency keys. A task whose
key is already pending is coalesced (rejected as a duplicate), not appended. The
key is released only on `complete()`. `RecoveryLedger` applies the same rule
across a crash: only in-flight-but-uncommitted tasks replay.

**Rationale:** the duplicate-segment failure is silent and compounding — a crash
during processing produces two copies of a segment, and both get broadcast.
Coalescing by key makes the failure structurally impossible rather than merely
unlikely. Tested: re-enqueue of a pending key is refused; crash replay skips
committed work.

---

## ADR-053: Audio cache reuse is gated on the full compatibility tuple

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §23.4: "A cached audio artifact should only be reused when the
script version and synthesis configuration are compatible."

**Decision:** `AudioCacheKey` carries script id, script version, synthesis
attempt, voice profile version, and synthesis config version. `get()` returns a
miss unless every field matches, verified against the stored key even after a
dict hit.

**Rationale:** reusing audio from an older script version would broadcast stale
text — the §15.4 stage 6 temporal-validation failure reintroduced through a
cache. A partial match (same script, newer version) must be a miss. The
double-check against the stored key guards against a hash collision returning
the wrong audio.

---

## ADR-054: The improvement loop cannot touch protected parameters, and never self-approves

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §28.2: "The system should not autonomously weaken factual validation
merely because doing so increases the number of segments successfully broadcast."
§28.3: every proposed improvement carries "an approval record." §28.4: evaluation
uses multiple dimensions, "not... one opaque score."

**Decision:** three structural guarantees.

1. `PROTECTED_PARAMETERS` (`validation_stages`, `provenance_enforcement`,
   `append_only_enforcement`, `contradiction_checking`,
   `attribution_enforcement`, …) are refused outright — a proposal touching them
   is `REJECTED`, not merely flagged.
2. `approve()` requires an explicit `approver` argument and a `TESTED` state; the
   engine never self-approves. `deploy()` additionally requires a rollback
   procedure.
3. `ImprovementEvaluator.compare()` reports per-dimension improvements and
   regressions. A regression beyond `MAX_REGRESSION` on any dimension blocks the
   improvement regardless of gains elsewhere — so a novelty gain that tanks
   factual grounding is rejected.

**Rationale:** §28.2 names the exact adversarial case: the system weakening
validation to broadcast more. A flag that could be overridden would eventually be
overridden. Refusal is the only durable form of the constraint. The
multi-dimension rule prevents the single-score failure §28.4 warns against.

---

## ADR-055: Continuous operation permits silence; filler must be disclosed

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §27.4: "When there is nothing valuable to add, it should be permitted
to remain silent or play an explicitly configured interstitial. An infinite
broadcast is a continuously available programming system, not an infinite stream
of fabricated novelty."

**Decision:** `ContinuousOperationPolicy.decide()` returns one of six
`SegmentNature` values. When no development, unresolved question, or historical
context is available, it returns `SILENCE` (permitted), not a forced segment.
Revisits and framed analysis carry `discloses_revisit=True`; a revisit without
disclosure is flagged `is_fabricated_novelty`.

**Rationale:** a system obligated to always talk will eventually present old
material as new. Permitting silence removes the pressure that produces the lie.
The disclosure flag makes §27.4's "the system knows when it is discussing
previously established material" a checkable property.

---

## ADR-056: Audio and metadata transports are separate types behind one seam

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §16.8: "The audio transport and metadata transport should be
logically separate." "SSE itself should not be treated as an audio codec." "A
later deployment may add Icecast... without requiring changes to the editorial
engine."

**Decision:** `AudioTransport` (a Protocol) and `MetadataTransport` are distinct
types. `StreamManager` holds one of each and exposes `transports_are_separate()`
as an assertion. `swap_audio_transport()` lets an Icecast sink replace the HTTP
one with no change to the buffer or engine.

**Rationale:** conflating the transports is how metadata ends up framed as audio
or audio as SSE. Making them separate types makes the conflation a type error
rather than a runtime surprise. The seam is what lets external streaming be
added later without touching editorial code.
