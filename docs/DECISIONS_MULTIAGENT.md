# Decisions — Multi-Agent Composition

The editorial-composition layer: single-voice structuring, multi-persona
dialogue, and the grounding floor that keeps both honest. These ADRs continue
the DECISIONS_PHASE* series (last was ADR-056).

## ADR-057: The model proposes structure; deterministic code decides what airs

**Status:** RATIFIED
**Date:** 2026-10-13

**Context:** §22.2 requires verification without a live model; §14.3 requires
that the model be given facts, not asked to discover them. A composing LLM
will pad, restate, and — observed live — invent facts (a persona cited
"Kellyanne Conway's 2016 appointment" absent from the source).

**Decision:** Every composed turn passes a deterministic `GroundingVerifier`
before it can air. Ungrounded turns are dropped; if none survive, the segment
is cancelled rather than broadcast. The model chooses order and connection;
code decides what is supported.

**Rationale:** trusting the model's good behavior fails on the first
hallucination. The floor is the difference between a broadcaster and a
confident liar. Fail-closed: when in doubt, air less.

## ADR-058: The grounding floor polices entities and figures, not vocabulary

**Status:** RATIFIED
**Date:** 2026-10-13

**Context:** A persona must be able to say a link is "speculative" or call
something a "pattern" (§12.3: personas shape significance, mechanism,
implication) without that counting as an invented fact. But it must not say
"Conway did X." Both look similar to a token matcher.

**Decision:** The floor checks only the bounded, checkable classes of
fabrication — capitalized proper nouns absent from the source
(`introduces_new_entities`) and numeric figures absent from the source
(`introduces_new_numbers`). Interpretive vocabulary is not policed.
`fabrication_reason()` combines both and names what was invented.

**Rationale:** an earlier attempt allowlisted analytical vocabulary and chased
the model's every word — unbounded and fragile. Named things and figures are
facts; reasoning-words are interpretation. The boundary is where fabrication
actually happens. A persona can question, contextualize, and connect; it
cannot populate the world with things that aren't there.

## ADR-059: Multi-persona dialogue is the default for substantial stories

**Status:** RATIFIED
**Date:** 2026-10-13

**Context:** A single voice structures facts but cannot surface genuine
analytical disagreement. §12.4 provides the machinery (independent first
passes, disagreement detection, guard); the engine had it but no LLM hook, and
single-fact stories have nothing to debate.

**Decision:** `_produce_segment` tries multi-persona dialogue first when a
story carries 2+ grounded facts; it falls back to the single-voice editor when
the disagreement guard suppresses (§12.5: no manufactured debate) or the
dialogue is not approved. Single-fact stories always use the editor.
`RuntimeConfig.multi_persona` toggles it.

**Rationale:** the right register depends on the story. Force dialogue on a
one-fact story and you manufacture conflict; force a single voice on a
substantial story and you flatten real disagreement. Let the evidence decide.

## ADR-060: A persona's position airs once, not once per pairwise disagreement

**Status:** RATIFIED
**Date:** 2026-10-13

**Context:** With three mutually-disagreeing personas, each position
participates in multiple pairwise disagreements. Emitting a turn per
disagreement spoke the same sentence 2-3× — a stutter on air. The "unresolved"
close listed the same proposition once per disagreement.

**Decision:** `DialogueComposer` dedupes spoken turns by (persona, text) and
dedupes `unresolved` propositions. The disagreement is still recorded in
`disagreements_addressed`; only the spoken turn is deduped.

**Rationale:** the record of what was debated and what is read aloud are
different things. The record stays complete; the audio does not repeat itself.
