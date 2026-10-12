# Epistemic Invariants

Source: spec §1, §3.4, §3.5, §5.6, §5.7, §5.8, §8, §10.6, §12.3, §14.7, §15,
§20.6, §27.5, §27.6.

This document is the **negative architecture**: the distinctions the system MUST
preserve. Every one is a testable invariant. If any of these collapses, the
system's core claim (evidence-grounded broadcasting) fails, regardless of how
fluent the output sounds.

## The chain of non-collapse (§1)

```
A news article is not an event.
An event is not a claim.
A claim is not evidence.
Evidence does not automatically establish a claim as true.
An interpretation is not a fact.
A persona's opinion is not the system's verified conclusion.
A generated sentence is not suitable for broadcast because a language model produced it.
```

Each must survive the entire pipeline. Tests: for each pair, construct a case
that distinguishes them and assert the system does not conflate them.

## I1. Status ≠ truth score

A claim's epistemic status is not a universal numerical measure of truth.

**A claim supported by five articles that all repeat the same original report is
not better supported than a claim supported by one directly observed primary
source.** Source independence, provenance, access to primary evidence, and the
nature of the claim matter.

Invariants:

- `CORROBORATED` requires genuinely distinct supporting evidence — repeated
  publication of one original assertion does not count.
- Source ownership groups are tracked; N outlets under one owner are not N
  independent confirmations.
- A calibrated model score is not a truth probability and must not be presented
  as one without validation.

## I2. Statement-existence ≠ statement-truth

An official transcript may strongly support the claim **that a statement was
made**. It does not necessarily support the **factual proposition contained in
that statement**.

Claim extraction must preserve attribution:

| Statement | Meaning |
|---|---|
| A minister said the policy had reduced inflation. | `ATTRIBUTED` claim about a speech act. |
| The policy reduced inflation. | `OBSERVATIONAL`/`CAUSAL` claim about the world. |
| An analyst argued the policy may reduce inflation. | `ATTRIBUTED` + `PREDICTIVE`. |

**The system must not silently transform an attributed statement or hypothesis
into an unqualified factual assertion.**

## I3. Correction ≠ evidence

A `Correction` records that a prior representation requires revision. `Evidence`
establishes or challenges propositions. These are different domain objects.

Invariants:

- Corrections have their own table/model, never stored as an Evidence row.
- A publisher correction ≠ the system discovering its own extraction was wrong.
- A publisher correction does not prove every associated claim false, but
  **requires reassessment** of dependent claims.
- A revised model assessment must not be mislabeled as a correction to the original source.
- The original record is never modified; corrections supersede, not overwrite.

## I4. Historical records are never rewritten

> **A material source revision must not erase the historical version used to
> produce an earlier broadcast.**

Invariants:

- Article versions chain via `supersedes_article_id`; the original remains.
- Corrections link to affected segments; the original transcript and audio
  remain historically identifiable.
- A correction does not replace the original transcript with revised wording —
  the audience and operator can distinguish **what was originally said** from
  **what the system now believes is supported**.
- Distinguish an ordinary metadata refresh from a substantive content correction.

## I5. Three time axes stay separate

Valid time ≠ observation time ≠ transaction time.

A report published Monday, discovered Wednesday, processed Thursday carries all
three. A Thursday snapshot must not imply Monday knowledge. `first_seen_at` and
`published_at` are deliberately distinct fields.

## I6. Personas cannot override evidence

**Personas can challenge interpretations, not arbitrarily override evidence.**
(§27.5)

A persona may argue a policy is unlikely to achieve its objective; it may
challenge assumptions behind a forecast or point out that the data does not
establish causation. It **must not** present a contradicted claim as an
established fact merely because that behavior matches its personality.

Personality may influence vocabulary, humor, pace, explanatory depth, analogies,
questions asked, interpretations emphasized, willingness to challenge another
persona. It **must not** independently determine whether a factual proposition
is true. The shared knowledge model provides a common evidence baseline.

Personas may disagree about significance while agreeing about content; disagree
about causal mechanism while agreeing about measurements. That distinction is
what makes analytical debate meaningful.

## I7. Multiple persona outputs ≠ corroboration

Using several personas does not guarantee independent reasoning. If every persona
uses the same model, receives the same framing, and retrieves the same
incomplete evidence, they may confidently reproduce the same mistake.

The system distinguishes:

- Multiple **personas**
- Multiple generation **attempts**
- Multiple **models**
- Independent source **evidence**
- Independent **verification methods**

> The system must never describe agreement among several outputs from one model
> as equivalent to independent factual corroboration.

This limitation must be **documented**, not hidden.

## I8. Article volume ≠ corroboration, novelty, or importance

- Volume of articles ≠ source diversity (syndication, ownership groups).
- Article surge from new evidence ≠ surge from repeated publication of one claim.
- Graph centrality ≠ real-world importance — a heavily covered subject becomes
  central simply because many articles were ingested.
- Attention ≠ importance: a celebrity controversy can attract more discussion
  than a major legislative decision.
- A late discovery can have **high** novelty (an investigation published today
  about an incident months ago). A newly published article can have **low**
  novelty if it repeats known information.

## I9. Near-duplicates may carry corrections

A near-duplicate article may contain a meaningful correction, a new quotation, or
an updated casualty figure. **Such content must not be discarded solely because
most of the article is unchanged.**

Deduplication produces candidate relationships and confidence assessments —
merges are not irreversible.

## I10. Not every conflict is a contradiction

Two sources reporting different unemployment rates may refer to different months
or use different measures. Identify those distinctions **before** marking claims
contradictory.

Contradiction categories to consider: direct contradiction, differences in
measurement, differences in temporal scope, different definitions, attribution
differences, stale information, apparent contradictions caused by article revisions.

`CONTRADICTED` means available evidence conflicts materially — it does **not**
automatically mean every component of the proposition is false.

## I11. Uncertainty attaches where it occurs (§9.4)

If the event is disputed, the event status reflects it. If the event is
established but the affected count is uncertain, uncertainty attaches to the
quantitative claims. If evidence is clear but causal interpretation is
uncertain, uncertainty attaches to the causal proposition.

**A single confidence score on an entire story is too coarse.** A story may
contain many well-established facts and one highly disputed claim. The world
model preserves this heterogeneity.

## I12. Generated ≠ validated ≠ delivered

The system distinguishes:

1. Text generated by a language model.
2. Text that passed validation.
3. Text submitted to TTS.
4. Audio successfully synthesized.
5. Audio placed in the delivery buffer.
6. Audio transmitted to a client.
7. Audio actually consumed by a listener (only with reliable playback telemetry).

**A server cannot infer a listener heard a segment because it wrote bytes to a
socket.** The UI and analytics must communicate these distinctions accurately.
A script generated but never played does not appear indistinguishably in the
public transcript.

## I13. Validation is multi-mechanism, not self-approval

The final validation step must not simply ask the model that wrote the script
whether its own output is correct (§15.6).

Mechanisms, each partial: deterministic claim-reference checks, rule-based
temporal checks, direct comparison with source passages, a separate verifier
prompt, a different model for high-impact segments, explicit contradiction
searches, human review for exceptional cases.

The objective is to catch errors through **multiple failure-independent
mechanisms**, not to treat a second LLM response as a guarantee of truth.

## I14. A source list is not provenance

A script is not approved merely because it includes a list of sources somewhere
in metadata. **The system must verify that the relevant evidence actually
supports the particular statement** (§15.3).

Retrieval must be able to answer "what evidence supports the claim that the
regulation reduced operating costs?" — returning evidence associated with the
causal proposition, relevant measurements, counterevidence, and appropriate
temporal scope. **A collection of articles that merely discuss the regulation is
not sufficient.**

## I15. Reporting an allegation ≠ asserting it is true (§20.6)

The system distinguishes between reporting an allegation and asserting its truth.
When a claim is unresolved, the broadcaster explains the uncertainty **without
either endorsing or suppressing the claim automatically**.

## I16. Config flags are not enforcement (§24.3)

`allow_speculation_as_fact: false` is a **policy declaration, not a substitute
for the script-validation mechanism that enforces it.** A configuration value
never replaces the deterministic check that makes it real.

## I17. Persona independence in analysis (§12.4)

When a segment requires multiple perspectives, initial analyses are generated
**independently**:

```
1. Construct a shared evidence packet.
2. Identify the question under discussion.
3. Generate an initial analysis for each assigned persona.
4. Record assumptions and evidence used by each analysis.
5. Identify substantive disagreements.
6. Generate a structured exchange addressing those disagreements.
7. Produce a final segment script.
8. Validate the script against the shared evidence packet.
```

Generating every persona response sequentially in one unconstrained conversation
causes later personas to inherit the framing of earlier responses. Independent
first-pass analysis reduces this risk; it does not eliminate correlated errors,
especially when all personas share one model — **document that limitation**.

## I18. The system does not manufacture disagreement or consensus (§12.5)

- Do not manufacture disagreement when evidence supports a straightforward conclusion.
- Do not force consensus when the underlying issue remains unresolved.

## I19. Controlled revision, not self-modification (§27.6)

The system may correct its understanding of events, revise claim assessments,
merge duplicate entities, and replace obsolete summaries — through **explicit
domain operations with provenance and validation**.

The language model must **not** be allowed to rewrite the database schema,
silently change editorial policy, or alter the meaning of historical records.

## I20. Never manufacture a breaking event (§2.2)

The system must never invent a breaking event to fill silence. Continuity comes
from fresh information plus deeper analysis of existing information — not from
an obligation to invent new facts. When nothing valuable can be added, it may
remain silent or play an explicitly configured interstitial.

## Invariant test plan

Each invariant above gets at least one test that:

1. Constructs the violating condition deliberately.
2. Asserts the system refuses, flags, or preserves the distinction.
3. Fails loudly (raises / rejects) rather than silently accepting.

See TESTING_STRATEGY.md §"Invariant tests" and ACCEPTANCE_CRITERIA.md Gate 3.
