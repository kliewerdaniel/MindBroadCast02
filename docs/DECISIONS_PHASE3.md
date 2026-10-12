# ADR Addendum 3 — Decisions Taken During Phase 3 Implementation

Companion to `DECISIONS_PHASE1.md` and `DECISIONS_PHASE2.md`.

---

## ADR-020: Default extractors are deterministic, not model-based

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §8.1 requires "a sequence of explicit passes rather than a single
opaque language-model operation," each with "a defined input, output, version,
and failure mode." The obvious implementation is to hand an article to an LLM
and ask for claims.

**Decision:** the default path is spaCy NER plus rule-based syntactic
classification. No language model is called anywhere in the compiler. A
model-based extractor can be substituted per-pass behind the same
`run(input) -> output` contract.

**Rationale:** §8.5 requires the system to answer "why did a previous compiler
version produce a different result?" A deterministic pass's behaviour is
reproducible from its version string alone. A model's is not, unless model,
prompt, and temperature are all pinned and versioned — at which point the
version string is carrying three independent variables.

**Consequence:** recall is lower than a model would achieve. That is an
accepted trade for inspectability, and it is reversible: the contract is what
makes substitution cheap.

---

## ADR-021: Attribution is checked before every other classification rule

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** `_classify()` must decide claim type from grammatical structure.
The ordering of those rules determines what the system is willing to assert.

**Decision:** attribution is checked first. A sentence with a reporting verb
and an attributed subject is `ATTRIBUTED` regardless of how factual its
content sounds. Hedge, normative, causal, and quantity rules are only reached
for unattributed sentences.

**Rationale:** the alternative ordering — checking for a quantity first, say —
would classify "Officials said 40 people died" as `QUANTITATIVE`, discarding
the attribution and presenting hearsay as a direct measurement. That is
invariant I2's most likely failure, and it would be invisible in output that
reads perfectly correct.

**Enforced by test:** the three §8.2 sentences, plus a test asserting that an
attributed claim's evidence relationship list contains no `SUPPORTS` entries.

---

## ADR-022: Evidence relationship is assigned structurally from claim type

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §8.2 requires preserving "the distinction between the existence of
a statement and the truth of that statement."

**Decision:** `ATTRIBUTED` claim → `ATTRIBUTES` relationship. Direct assertion
→ `SUPPORTS`. The assignment is derived from the claim type the previous pass
produced, not judged per-passage.

**Rationale:** a transcript strongly evidences that a statement was made and
says nothing about whether its content is true. If that distinction were left
to a per-passage judgement, it would eventually be made inconsistently — and
the inconsistency would look like evidence.

---

## ADR-023: Pass 7 compares qualifiers before declaring a contradiction

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §8.2 warns that "two sources may report different unemployment
rates because they refer to different months or use different measures."

**Decision:** extract quantity + unit + temporal qualifier, then:

- same unit, same temporal scope, different value → `CONTRADICTS`
- different temporal scope → `TEMPORAL_DIFFERENCE`, relationship `QUALIFIES`
- same value, same scope → `CORROBORATION`
- different speakers, same proposition → `ATTRIBUTION_DIFFERENCE`

**Rationale:** flagging a definitional difference as a contradiction would
present a false finding with the same confidence as a real one. Invariant I10
exists because that error is worse than missing a contradiction.

**Calibration note:** `_shares_subject` requires only 1 shared significant word.
The first version required 2, which silently filtered out the canonical
contradiction case — "unemployment rose to 6.1% in April" vs "unemployment was
5.2% in March" share exactly one significant word once values and months are
stripped. The symptom was an empty report, which looks like "no conflict found"
rather than "the check did not run."

---

## ADR-024: CKU is a read-only view; no `cku` table exists

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §8.3 leaves the abstraction boundary open but states a preference:
"The safest initial design is to treat CKUs as a stable interface over existing
domain objects, rather than introducing a second competing system of record."

**Decision:** `CKUAssembler.for_claim()` queries Claims, Evidence, and
ClaimEvidence. It writes nothing. No `canonical_knowledge_units` table.

**Rationale:** a second store of truth would require re-enforcing every Phase 1
invariant — append-only provenance, correction chains, snapshot completeness —
in two places. Tested by asserting the table does not exist.

**CKU identity** is (claim type + normalized subject/predicate/object +
quantities + temporal qualifiers), explicitly not the proposition text, per
§8.3.1. Tested: same assertion with different wording shares an identity;
identical sentence with a different quantity does not.

---

## ADR-025: A compilation refuses to UPDATE the captured article

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** the first integration pass ended with
`UPDATE articles SET extraction_status = 'succeeded'`. Phase 1's append-only
trigger rejected it with "articles is append-only: supersede instead."

**Decision:** record the transition as an `ArticleCompiled` domain event
instead of mutating the row.

**Rationale:** the trigger was correct and the integration pass was wrong. The
captured article is a historical record of what was fetched; whether it has
since been compiled is a processing fact, and processing facts belong in the
event log. Mutating the capture would also make `supersede_article`'s version
chain ambiguous about what actually changed in the source.

**Worth recording:** this is the second time a Phase 1 invariant caught a later
phase doing something wrong (the first was `UNIQUE(content_hash)` blocking
syndication). The invariants are earning their cost.
