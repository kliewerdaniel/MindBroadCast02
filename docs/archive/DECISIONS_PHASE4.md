# ADR Addendum 4 — Decisions Taken During Phase 4 Implementation

Companion to `DECISIONS_PHASE1.md` through `DECISIONS_PHASE3.md`.

---

## ADR-026: The repetition check runs before any type-specific rule

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** `ChangeDetector.detect()` classifies each incoming claim. The
first implementation checked claim *type* first — quantity, causal, decision —
and only checked for repetition in the generic branch at the end.

**Consequence:** a restated figure hit the `quantitative` branch, saw a number
the system already knew, and was classified `REVISED_NUMBER`. Ten articles
reprinting one wire report produced ten "material revisions," which is exactly
the article-volume-as-development error §9.2 exists to prevent — and it would
have propagated into novelty and velocity, making the broadcaster announce the
same thing repeatedly.

**Decision:** the repetition check is the first thing evaluated, before any
type-specific branch.

**Found by:** a test asserting that ten restatements yield zero material
change. The symptom was not a crash — it was a plausible-looking finding.

---

## ADR-027: Velocity decays from observation time, not publication time

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §9.3 requires that "an investigation published today may
establish details about an incident that occurred months ago" and that the
system "should not necessarily rank this information as old merely because the
underlying event is old."

**Decision:** the recency term in `V_s(t) = Σ W(d)·exp(−(t−t_d)/τ)` uses
`t_d` = the change's `occurred_at`, which the change detector sets from the
article's **observation** time. `τ` varies by story type (`emergency` 30min,
`general` 6h, `policy` 24h, `investigation` 72h).

**Rationale:** using publication time would rank a months-old incident's fresh
investigation as stale, which is the precise error §9.3 names.

---

## ADR-028: Audience relevance is a capped additive term, never a multiplier

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §10.7 requires that "audience relevance must not automatically
override importance or evidence quality" and warns against "a feedback loop in
which popular stories receive increasing coverage."

**Decision:** audience enters as an additive term capped at
`MAX_AUDIENCE_CONTRIBUTION = 0.5`, with `weight` forced to 1.0 so the cap is
the actual bound.

**Rationale:** a multiplier would let audience relevance scale importance, so
a sufficiently popular story would outrank any consequential one. The cap makes
that structurally impossible rather than merely unlikely. Tested: a pure-
popularity story scores −0.35 net against 3.45 for a story with 100,000 people
affected, irreversibility, a safety implication, and independent primary
evidence.

---

## ADR-029: Story clustering is deterministic, with no embeddings

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §5.5.1 requires that the system "should not create a completely
unrelated editorial identity for every article about the proposal."

**Decision:** cluster on canonical identity (the qualifier-aware CKU identity
from Phase 3) or ≥2 shared entities. `MIN_SHARED_ENTITIES = 2` because one
shared entity — "the government" — appears in unrelated reporting.

**Rationale:** §8.4 says embeddings supplement structured relationships rather
than replacing them. A clustering decision that cannot explain itself cannot be
audited under Gate 5, and an embedding-similarity merge would repeat the mistake
§5.4 forbids for entities. Deterministic clustering is inspectable and
reversible.

**Accepted limitation:** recall is lower than semantic clustering would
achieve. Two articles about one development with no shared entity and no
shared qualifier will not cluster.

---

## ADR-030: Records with no observation time are excluded from replay, not assumed current

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §22.7 requires that replay "should only receive information that
would have been available by that time," to prevent temporal leakage.

**Decision:** a record whose `first_observed_at` is `None` is excluded from the
replay window **and recorded as a violation**.

**Rationale:** an unknown timestamp is not evidence of availability. Assuming
such a record is current would let unknowable information into an evaluation,
which is the subtler half of leakage — the loud half (a future record) is easy
to catch, but a record with no time at all slips through a naive filter.

**Consistency:** this is the same principle as ADR-012 (legacy missing
timestamps fall back to the epoch, never "now"). Both refuse to fabricate
availability.
