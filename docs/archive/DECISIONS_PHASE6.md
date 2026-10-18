# ADR Addendum 6 — Decisions Taken During Phase 6 Implementation

Companion to `DECISIONS_PHASE1.md` through `DECISIONS_PHASE5.md`.

---

## ADR-037: A persona update creates a version; it never rewrites in place

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §12.2: "A persona update should create a versioned configuration
change rather than silently rewriting the instructions used by a previously
generated segment."

**Decision:** `PersonaConfig.revised()` returns a new config with an
incremented `configuration_version`, leaving the original untouched.
`PersonaRegistry` retains every version and exposes `version_at()`, so a
segment generated under version 1 traces to the instructions that produced it.

**Rationale:** a silent in-place rewrite would make previously broadcast
segments untraceable to their instructions — an audit failure, and it would
make §12.7 consistency evaluation meaningless (you could not tell whether the
persona drifted or was reconfigured).

---

## ADR-038: Independent first-pass analysis is enforced by ordering, not by prompt

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §12.4: "Generating every persona response sequentially in one
unconstrained conversation can cause later personas to inherit the assumptions
and framing of earlier responses. Independent first-pass analysis reduces this
risk."

**Decision:** `IndependentAnalysisRunner.run_first_passes()` completes every
persona's analysis from the shared packet alone, before any exchange is
composed. Each analysis function receives `(config, packet)` and nothing else.
The `DialogueOrchestrator` composes the exchange only after all first passes
return.

**Rationale:** the reduction of framing-inheritance is structural, not a
matter of prompt wording the model might override. A function physically cannot
read another persona's first pass because it has not been returned yet.

**Accepted limitation, recorded per §12.4 and §14.7:** this "does not eliminate
correlated errors, especially when all personas use the same underlying model."
The imitation detector surfaces the symptom; the fix is genuinely independent
models, an infrastructure decision outside this phase.

---

## ADR-039: Consistency evaluation scores *unexplained* change, not change

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §12.7: "This does not mean the persona must never change its mind.
It means changes should be explainable through new evidence, revised
assumptions, or explicit configuration updates. A persona that becomes more
cautious after several incorrect predictions may represent a legitimate change
in analytical behavior. A persona that randomly changes its political or
economic outlook from one segment to the next indicates a consistency problem."

**Decision:** the `appropriate_revision` dimension scores 1.0 when a position
change is accompanied by a CORRECTION memory entry (recorded evidence basis)
or an explicit config update, and 0.3 when the change has no recorded basis.
Change itself is not penalized.

**Rationale:** scoring change as drift would flag a persona that correctly
revises after bad predictions — the opposite of the spec's intent. The
distinction is *explainability*, and the explanation is a recorded artifact,
not an assertion.

---

## ADR-040: Memory is bounded on active entries, with a bounded withdrawal trail

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §12.6: memory "must not become an uncontrolled transcript appended
to every prompt," and "should be persistent but bounded." But it must also let
a persona "remember that it previously argued for a position while also
recognizing that subsequent evidence weakened that position."

**Decision:** the cap limits ACTIVE entries per type. Withdrawn/superseded
entries are retained up to the same cap and dropped oldest-first. So active
memory is hard-bounded, and a recent withdrawal stays visible long enough to be
read, but churn cannot grow the store without bound.

**Bugs found and fixed while testing this:**
- The first implementation marked a forced-out ACTIVE entry WITHDRAWN then
  immediately popped it, so the withdrawal vanished silently — the audit trail
  it was meant to create was erased on creation. Caught by
  `test_forced_eviction_marks_withdrawal`.
- A second design retained every withdrawn entry with no cap, letting total
  size grow to unbounded through churn — contradicting "bounded." Caught by
  `test_memory_is_bounded_per_type`.
  The reconciled design above bounds both, separately.

---

## ADR-041: A correction must withdraw what it revises across memory-type boundaries

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §12.6 lists CORRECTION memory ("prior conclusions that were
revised or withdrawn") as a type distinct from SEMANTIC memory. A correction of
a stable conclusion is therefore a CORRECTION entry revising a SEMANTIC entry —
in different type buckets.

**Bug found and fixed:** the original revision logic searched only the same
memory-type bucket, so a CORRECTION revising a SEMANTIC conclusion never
withdrew the original — the original stayed ACTIVE alongside its own
retraction. Caught by `test_recall_filters_inactive_by_default`, which
silently read as "no conflict" because both entries were active.

**Decision:** the revision search spans all of a persona's buckets.

---

## ADR-042: Disagreement suppression requires divergent positions, not just unanimous metadata

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §12.5: "The system should not manufacture disagreement when the
evidence supports a straightforward conclusion."

**Bug found and fixed:** the original `should_suppress` checked that all
personas shared the same facts and evidence, and suppressed if so — without
checking that their *positions* agreed. Two personas with identical (including
empty) fact sets but genuinely opposite positions were suppressed as
"manufactured disagreement," airing nothing. Caught when the full
orchestration sequence was suppressed despite a real detected disagreement.

**Decision:** the position check is primary. Suppression requires that
positions do not substantively diverge, verified by re-running the detector.
An empty fact set is trivially "equal" and must never be mistaken for
consensus.

**Rationale:** §12.3 explicitly allows personas to "disagree about the
significance of a policy while agreeing about what the policy contains."
Suppressing on shared metadata suppresses exactly the substantive
disagreement the system exists to surface.
