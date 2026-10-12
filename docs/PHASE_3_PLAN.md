# Phase 3 Plan — The Knowledge Compiler

**Objective:** Transform articles into structured claims and evidence.
(Spec §25 Phase 3, §8)
**Gate 3:** Claims and evidence have traceable provenance.

**Entry state:** Phases 0–2 complete, Gates 1–2 MET — 357 tests passing.

---

## The central design problem

Spec §8.1: "The compiler should be designed as a sequence of explicit passes
rather than a single opaque language-model operation. Each pass has a defined
input, output, version, and failure mode."

That sentence is the whole phase. The temptation is to hand an article to an
LLM and ask for claims. That produces fluent output with no traceable
provenance, which fails Gate 3 and violates invariant I14.

**So the default extractors in this phase are deterministic, not model-based.**
spaCy provides dependency parses for attribution detection; regex and sentence
segmentation handle quantities and temporal qualifiers. A language-model
extractor can be added later behind the same interface — but the *default* path
must be inspectable, because:

- §8.5 requires every pass to record its version, and the system must answer
  "why did a previous compiler version produce a different result?"
- A deterministic extractor's behaviour is reproducible. A model's is not,
  without pinning model + prompt + temperature and versioning all three.

## Pass structure

Each pass is a class with `name`, `version`, `run(input) -> output`, and a
declared failure mode. Passes are individually testable and individually
versioned.

| Pass | Spec | Module | Default implementation |
|---|---|---|---|
| 1 Structural normalization | §8.2 | `structural.py` | Sentence/paragraph segmentation with **text offsets** |
| 2 Entity extraction | §8.2 | `entities.py` | spaCy NER; records span + method; **never auto-merges** |
| 3 Claim extraction | §8.2 | `claims.py` | Sentence classification into claim types; attribution preserved |
| 4 Evidence extraction | §8.2 | `evidence.py` | Passage→proposition association; `ATTRIBUTES` ≠ `SUPPORTS` |
| 5 Entity resolution | §8.2 | `resolution.py` | Deterministic candidate → explicit merge decision |
| 6 Event resolution | §8.2 | `events.py` | Classify: existing / update / distinct / several / uncertain |
| 7 Contradiction analysis | §8.2 | `contradiction.py` | Distinguish measurement/temporal/definition differences |
| 8 Knowledge integration | §8.2 | `integration.py` | One transaction; partial status explicit |

Plus `cku.py` (§8.3) — a **read-only interface over existing domain objects**,
per the spec's explicit guidance, not a second system of record.

## The three requirements that will bite

### R1. Attribution must survive extraction (invariant I2)

§8.2 gives three sentences that mean different things:

- "A minister said that the policy had reduced inflation."
- "The policy reduced inflation."
- "An analyst argued that the policy may reduce inflation."

The system must not silently transform an attributed statement into an
unqualified factual assertion. **This drives the claim-type classification:**
a sentence with a reporting verb + attributed subject becomes `ATTRIBUTED`,
not `OBSERVATIONAL`. Test: all three produce different claim types from
structurally similar sentences.

### R2. Evidence existence ≠ evidence truth (§8.2, I2)

An official transcript strongly supports *that a statement was made*. It does
not support the *factual proposition* in that statement. So the
`ClaimEvidence.relationship_type` for a transcript excerpt is `ATTRIBUTES`,
never `SUPPORTS`. Already tested at the persistence layer; the compiler must
produce the right relationship, not just the right schema.

### R3. Not every conflict is a contradiction (§8.2, I10)

Two sources reporting different unemployment rates may refer to different
months or measures. Pass 7 must attempt to identify those distinctions
**before** marking claims contradictory. Implementation: extract quantity +
unit + temporal qualifier, and only flag `CONTRADICTS` when the qualifiers
match and the values differ. Differing qualifiers → `QUALIFIES` or
`PROVIDES_CONTEXT`.

## Dependency tracking (§8.5)

The compiler must not require a full rebuild when one component changes. Each
extracted object records `created_by_component` + version, and a
`compilation_dependencies` table records which derived objects depend on which
inputs, so a new article version invalidates only its dependents.

## Explicitly out of scope

- Embeddings / semantic retrieval (§8.4) — deferred; spec says SQLite FTS
  first, vector DB only on measured need. FTS indexing is in scope.
- Story creation and editorial ranking — Phase 4.
- Any model-based extraction — the interface is designed for it, the default
  is deterministic.

---

## Work streams

### A — Structural normalization (`structural.py`)

Sentence and paragraph segmentation with **character offsets into the
original text**. Offsets are load-bearing: §8.2 requires "enough location
information to identify the original passage from which a claim or evidence
excerpt was extracted."

**Tests:** offsets are monotonic and non-overlapping; reassembling spans
reproduces the source; empty and single-sentence input handled.

### B — Entity extraction (`entities.py`)

spaCy NER → candidate mentions with `(start, end, text, label)`. Records
extraction method and version. **Never merges into a canonical entity** — that
is Pass 5's job, and only with an explicit decision.

**Tests:** span offsets point at the right substring; method/version recorded;
no merge occurs as a side effect.

### C — Claim extraction (`claims.py`)

Sentence → claim with type. Attribution detection via reporting verbs
(`said`, `announced`, `argued`, `according to`, `claimed`) + attributed
subject. Quantity/unit and temporal qualifier extraction.

**Tests (the R1 cases):** the three §8.2 sentences produce `ATTRIBUTED`,
`OBSERVATIONAL`, `ATTRIBUTED`+`PREDICTIVE` respectively; a quantitative claim
carries its unit; attribution is never dropped.

### D — Evidence extraction (`evidence.py`)

Passage → proposition. Relationship assigned by structure: an attributed
statement's passage is `ATTRIBUTES`; a direct assertion's passage is
`SUPPORTS`. Never the reverse.

**Tests (R2):** transcript excerpt → `ATTRIBUTES`, and asserting `SUPPORTS`
is absent.

### E — Resolution (Passes 5, 6)

Entity resolution: deterministic candidates (exact alias, same canonical
name+type) merge automatically; ambiguous stay unresolved. Event resolution:
classify the five §8.2 outcomes, recording basis + confidence.

**Tests:** exact match merges; ambiguous does not; event classification covers
all five outcomes.

### F — Contradiction analysis (Pass 7)

Compare a new claim against existing claims on the same normalized subject.
Extract quantity + unit + temporal scope. `CONTRADICTS` only when qualifiers
match and values differ; otherwise `QUALIFIES`.

**Tests (R3):** same month, different value → `CONTRADICTS`; different months,
different values → **not** `CONTRADICTS`.

### G — Integration (Pass 8) + CKU

One transaction: claims, evidence, links, entities, events, dependency rows,
domain events, outbox message. Partial processing flagged explicitly.
CKU as a read-only view.

**Tests:** a failed integration leaves nothing partial that looks complete;
CKU assembles a claim + its evidence without a second table; dependency rows
invalidate correctly.

---

## Exit criteria — Gate 3

- [ ] A structured claim traces to the source passage it came from, by offset.
- [ ] Extraction confidence is distinct from evidentiary support.
- [ ] Attribution survives extraction (the three §8.2 sentences differ).
- [ ] `ATTRIBUTES` is never recorded as `SUPPORTS`.
- [ ] Conflicting numbers with different temporal scope are not contradictions.
- [ ] Every extracted object records extractor + version.
- [ ] Dependency tracking invalidates only affected derived objects.
- [ ] CKU is an interface over existing objects, not a new table of record.
- [ ] Full suite green.
