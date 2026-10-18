# ADR Addendum 2 — Decisions Taken During Phase 2 Implementation

Companion to `DECISIONS_PHASE1.md`. These emerged from building ingestion.

---

## ADR-015: `content_hash` uniqueness is scoped to `(source_id, content_hash)`

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** Phase 1 added `UNIQUE(content_hash)` on `articles` as the dedup
key. Phase 2 immediately hit the consequence: a *different* outlet reprinting
the same wire report was rejected by the schema, so syndication could not be
recorded at all — and syndication is precisely the relationship the system
needs in order to avoid counting one wire report as N independent
confirmations (invariants I1/I8).

**Decision:** `UNIQUE(source_id, content_hash)`. The same source publishing
identical text twice is still a duplicate capture; a different source
reproducing the same text is syndication and is retained.

**Rationale:** the uniqueness constraint encodes "this source has already
captured this content," not "this content exists." Conflating the two makes
the system unable to represent the fact it most needs to represent.

---

## ADR-016: `validate_targets=False` exists, and is test-only

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** the SSRF guard correctly refuses non-resolving fixture hostnames,
which made pipeline tests impossible without either weakening the guard or
standing up a local HTTP server.

**Decision:** `Fetcher(validate_targets=False)` skips URL validation. It is
documented as test-only, and the SSRF policy is covered exhaustively in its
own test class where it belongs.

**Accepted risk:** if a future caller sets `validate_targets=False` in
production, the guard is off for that path. Mitigation: the flag defaults to
True, the docstring says test-only, and `TestSecurityPolicy` does not use it.

---

## ADR-017: `content_changed` is a field, not something inferred from prose

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** the first implementation of `DuplicateCandidate.should_discard`
distinguished a re-observation from a material revision by substring-matching
the human-readable `reason` field. That is fragile — rewording a log message
would silently change whether a corrected article is discarded.

**Decision:** an explicit `content_changed: bool` on the candidate, set by
`classify()`.

**Rationale:** the discard/supersede decision is load-bearing (criterion 9,
invariant I4). Behaviour must not depend on prose. This bug was caught by a
test, which is the argument for the test existing.

---

## ADR-018: Ingestion creates no claims, events, or stories

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** spec §7.1 says ingestion "should not determine the final
editorial meaning of every article." It would be easy — and wrong — to have
the pipeline start minting Events because a feed item looks like an event.

**Decision:** the pipeline writes only `articles` rows, `ArticleCaptured`
domain events, and (for retained near-duplicates)
`DuplicateCandidateDetected` events. Entity, claim, evidence, event, and story
creation is Phase 3.

**Enforced by test:** `TestIngestionDoesNotInterpret` asserts that after a
fetch mentioning "the minister said inflation fell," claim/event/evidence/
story counts are all zero.

---

## ADR-019: Near-duplicate threshold defaults to 0.50, calibrated downward on purpose

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** the first default was 0.75. Testing against a realistic wire
report reprinted with one changed casualty figure scored only ~0.67 with
5-word shingles, so the default missed exactly the case spec §7.5 says must
not be discarded.

**Decision:** default 0.50, documented with the calibration measurement.

**Rationale:** the two error modes are asymmetric. Over-detection costs a
stored candidate relationship; under-detection silently loses a correction.
The threshold is set on the side of the recoverable error.
