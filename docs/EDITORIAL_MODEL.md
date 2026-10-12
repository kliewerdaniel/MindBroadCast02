# Editorial Model

Source: spec §10, §11, §2.2.

## The editorial problem (§10.1)

The editorial engine decides what the broadcaster discusses next. This is **not**
sorting articles by publication time, ranking by popularity, or asking an LLM
what is interesting.

It is a **constrained scheduling problem**: allocate finite broadcast time across
an effectively unbounded topic set, where each topic differs in importance,
novelty, evidentiary support, audience relevance, unresolved questions,
historical significance, and redundancy — while accounting for the cost of
changing subjects, prior coverage depth, and the value of continuity.

### Formal statement

Let S_t be the set of eligible stories at time t:

```
P(s,t) = f(I_s, N_s, V_s, E_s, R_s, U_s, A_s, C_s, D_s)
```

| Factor | Meaning |
|---|---|
| `I_s` | Estimated real-world importance. |
| `N_s` | Novelty of available information. |
| `V_s` | Temporal velocity of the story. |
| `E_s` | Evidentiary quality and relevance. |
| `R_s` | Relationship to other important developments. |
| `U_s` | Value of unresolved questions. |
| `A_s` | Audience relevance. |
| `C_s` | Coverage continuity and editorial context. |
| `D_s` | Redundancy / repetition penalty. |

**These are conceptual variables and must be operationally defined before being
combined into a production ranking.** The score is always accompanied by
explanations describing which factors materially influenced the ranking. A high
score must not conceal that a story ranked highly because of speculation,
popularity, or an unreliable source.

### Selection objective (§10.10)

```
max  Σ x_{s,t} [ P(s,t) − λR(s,t) − μT(s,t) ]
s,t

subject to:  Σ_s x_{s,t} ≤ 1
```

- `x_{s,t}` — whether story s is allocated to a broadcast slot.
- `P` editorial value, `R` redundancy, `T` transition cost.
- `λ`, `μ` — relative penalty weights.

Real implementations may use a simpler greedy ranking algorithm. The
formulation is valuable because it **makes the problem explicit** — it is not a
requirement to introduce a complex solver before the scoring features are
measurable.

## Importance (§10.2)

Potential real-world significance of a development. Factors: number of people
affected, scale of economic consequences, legal/institutional significance,
safety implications, geopolitical consequences, irreversibility of the decision,
long-term effects, relevance to ongoing major events, potential to materially
change public understanding.

**Importance is not defined by how many people are discussing a subject.**
Attention and importance are related but distinct; the system must be capable of
recognizing a major legislative decision outranks a celebrity controversy with
more chatter.

## Novelty (§10.3)

How much newly available information changes the system's current understanding.
Calculated **against the existing world model**, not against article text alone.

```
N_s = w₁·N_claims + w₂·N_events + w₃·N_evidence
      + w₄·N_relationships + w₅·N_corrections
```

Weights are tunable parameters, not universal constants.

Forms of novelty to recognize:

- A completely new event.
- A new claim about an existing event.
- New evidence for an existing claim.
- A change in claim status.
- A newly discovered relationship between events.
- A correction that invalidates an earlier account.
- A new consequence of an established event.

Repeated content is penalized, but the system preserves the ability to revisit a
story when a genuinely consequential development occurs.

## Temporal velocity (§10.4)

The rate at which a story develops. A story with several meaningful developments
in a short period may deserve more immediate attention than a static story of
similar importance.

```
V_s(t) = Σ_{d ∈ D_s} W(d) · exp( −(t − t_d) / τ )
```

- `D_s` — set of detected **material** developments.
- `W(d)` — estimated significance of development d.
- `t_d` — observation or effective development time.
- `τ` — decay parameter.

**Velocity must be measured using meaningful changes, not article volume.**
Distinguish a surge from new evidence from a surge from repeated publication of
one claim. `τ` varies by story type — a rapidly evolving emergency and a
long-running policy discussion have different temporal characteristics.

## Knowledge graph centrality (§10.5)

Identifies stories connected to many important entities, events, or unresolved
questions. Useful: one policy decision connecting several economic developments,
court cases, and institutional changes.

**Centrality is not the same as real-world importance.** A heavily covered
subject becomes central simply because many articles were ingested; a new but
consequential event initially has few connections. Centrality is **normalized
and used as a contextual feature**, not a decisive ranking mechanism.

Candidate measures: degree centrality, weighted degree, temporal centrality,
PageRank-style propagation, connectivity to explicitly designated high-impact
entities, number and importance of newly established relationships.

If a PageRank-like measure is used, the graph's **nodes, edges, weights, and
update procedure must be defined explicitly** — otherwise the score becomes an
opaque proxy for source volume.

## Evidence quality (§10.6)

Influences **broadcast eligibility as well as ranking**. Distinguish:

| Class | Description |
|---|---|
| Primary documentary evidence | Official records, filings, datasets. |
| Directly attributable statements | On-record quotes with a named source. |
| Independent corroboration | Distinct sources, distinct ownership. |
| Secondary reporting | Reporting that relays another source. |
| Anonymous / weakly attributable assertions | |
| Unsupported social-media claims | |
| Conflicting accounts | |
| Explicitly corrected material | |

Evaluate whether the evidence is **appropriate for the claim being made**. A
primary source is highly reliable for the fact that an institution issued a
statement, less conclusive about the accuracy of that statement's contents.

Source **ownership and syndication relationships** are considered when assessing
independence. A story supported by independent evidence from several sources is
not equivalent to one supported by many outlets repeating one original report.

## Audience relevance (§10.7)

Inputs: explicit topic subscriptions, direct listener requests, repeated
requests, voluntary feedback, playback behavior, requests to avoid/reduce
categories, recent programming history.

Rules:

- Audience relevance **must not automatically override importance or evidence quality**.
- **Avoid the popularity feedback loop:** popular stories get more coverage →
  more interaction → still more coverage. Audience signals are **bounded**, and
  the editorial system preserves room for important developments that have not
  yet attracted attention.
- Preference updates are explainable and reversible where practical.
- Feedback is **noisy evidence about preferences**, not a measure of truth: a
  replay may indicate confusion; a skip may reflect timing or technical problems.

## Coverage debt (§10.8)

The difference between the importance of a story and the depth of attention the
broadcaster has given it.

A major event that received only a brief announcement leaves the audience
without context. Another event may have received ten repetitive segments without
adding understanding.

Accounts for: importance, previous coverage depth, time since the last
substantive explanation, unresolved questions, missing historical context,
unexamined consequences, previous corrections requiring clarification.

**Coverage debt is the mechanism for returning to important subjects even when
no new article has appeared**, and for avoiding the mistake of treating
repetition for depth.

## Coverage saturation (§10.9)

A story becomes less eligible for repeated coverage when the system has nothing
materially new to say. A story may be saturated because:

- No new evidence has appeared.
- The major implications have already been explained.
- Recent segments repeated earlier content.
- Important unresolved questions cannot currently be answered.
- Other stories have substantially greater marginal value.

**Saturation must not permanently suppress a story.** New evidence or a material
change reduces the saturation penalty.

## Explainable story selection (§10.11)

Every selected story gets a recorded selection rationale derived from
**structured decision features**:

```json
{
  "story_id": "story-123",
  "decision": "SELECTED",
  "reasons": [
    "New primary-source document",
    "Material change to an active claim",
    "High unresolved-question value",
    "Low recent coverage depth"
  ],
  "exclusions": [],
  "ranking_version": "editorial-ranker-v1",
  "decision_time": "2026-10-12T16:00:00Z"
}
```

A language model may produce a human-readable explanation, **but the explanation
must not replace the underlying decision record.**

## Editorial fairness and selection bias (§10.12)

A continuously operating broadcaster can develop systematic bias through source
selection, ranking weights, audience feedback, and historical coverage patterns.

Monitor:

- Source concentration.
- Ownership-group concentration.
- Geographic distribution of coverage.
- Topic distribution.
- Frequency of revisiting the same entities.
- Differences between high-attention and high-importance stories.
- Proportion of claims supported by primary vs. secondary sources.
- Number of disputed claims presented in definitive language.
- Whether certain topics are repeatedly excluded by the ranking model.

These measurements **do not mechanically establish editorial fairness** — they
reveal patterns requiring inspection. The system retains enough information to
distinguish bias originating in the **source corpus** from bias introduced by
**extraction, ranking, generation, or editorial policy**.

## Segment types (§11.3)

| Type | Purpose |
|---|---|
| `HEADLINE` | Concise announcement of a significant development. |
| `NEWS_REPORT` | Structured reporting of a recent event. |
| `EXPLAINER` | Background, definitions, causal context. |
| `ANALYSIS` | Interpretation of evidence and implications. |
| `DEBATE` | Structured disagreement between personas. |
| `FOLLOW_UP` | Reassessment of a previously covered story. |
| `CORRECTION` | Explicit revision of an earlier broadcast claim. |
| `AUDIENCE_RESPONSE` | Response to a listener question or request. |
| `HISTORICAL_REVIEW` | Reconstruction of an issue's development. |
| `SYNTHESIS` | Analysis of relationships among multiple stories. |
| `TRANSITION` | Short connective speech between segments. |
| `FILLER_ANALYSIS` | Clearly identified background discussion when little new reporting is available. |

`FILLER_ANALYSIS` is an **internal classification**, not necessarily the phrase
spoken to listeners. The requirement: the system knows when it is discussing
previously established material rather than reporting a new development.

## Segment duration (§11.4)

Original design target: ~10–40 seconds per broadcast turn — a default operating
range, not a rigid constraint.

| Policy | Range |
|---|---|
| Short headline | 10–15 s |
| Standard turn | 15–30 s |
| Extended analytical turn | 30–40 s |
| Longer explanation | Multiple linked turns |
| Exceptional breaking update | As short as necessary to communicate supported information |

The scheduler reasons in **expected spoken duration**, not word count:

```
D_estimated = 60W / R      (W = spoken words, R = expected words per minute)
```

At 150 wpm, 25 words ≈ 10 s; 40 words ≈ 16 s. The estimator is calibrated
against actual TTS output, since duration varies by voice, language,
punctuation, speaking rate, and synthesis configuration.

## Story transitions (§11.5)

A transition may explain: why the next story matters, how it relates to the
current one, why a previous discussion is revisited, why the broadcast is moving
away from a developing event, which question the next segment addresses.

**The transition must not invent a connection merely to make unrelated stories
sound unified.** If two stories are connected only by an analytical hypothesis,
the broadcaster communicates that status explicitly.

## Control plane discipline (§11.1)

The editorial control plane converts the world model into a coherent broadcast
plan, managing: story selection, segment structure, persona assignment,
duration, broadcast continuity, transitions, breaking-news interruptions,
audience questions, current audio state, available evidence, and model/TTS
availability.

**It must not be implemented as an unconstrained LLM agent that receives the
entire world database and decides what to do.** The control plane uses explicit
state, deterministic scheduling rules, and narrowly scoped model proposals.

## Priority classes (§23.2)

| Priority | Work |
|---|---|
| 0 | Active broadcast delivery — continuity for approved audio already being delivered. |
| 1 | Urgent editorial developments — sufficiently important breaking information and required corrections. |
| 2 | Current knowledge integration — recently captured articles, affected story updates. |
| 3 | Routine editorial planning — future segments, ranking updates. |
| 4 | Background enrichment — embeddings, revisiting old stories, improving summaries, optional reanalysis. |

The system defers expensive background tasks when immediate operational work
requires resources.

## The Recursive Improvement Layer (§28) — deferred

A separate subsystem (not an extension of the Broadcast Director) that evaluates
the broadcaster's own artifacts: extracted claims, evidence relationships, story
rankings, editorial plans, persona analyses, scripts, validation outcomes, audio
segments, listener feedback, corrections, operational metrics.

```
Observe → Evaluate → Identify Failure → Propose Change → Test
        → Approve → Deploy → Measure
```

**The critical distinction is between identifying a failure and authorizing a
change.** Initially limited to low-risk, reversible parameters (polling
intervals within limits, background task order, bounded ranking weights in
simulation, cache policies, redundant model-call detection, regression-test
suggestions).

> **The system must not autonomously weaken factual validation merely because
> doing so increases the number of segments successfully broadcast.**

### Avoiding self-reinforcing evaluation (§28.4)

The system must not define success exclusively through metrics it can easily
optimize: rewarded only for listening time → sensationalism; only for factual
conservatism → useless caution; only for published volume → redundant segments.

```
Q = f(F, P, N, C, D, R, S)
    factual grounding | provenance integrity | novelty |
    clarity | analytical depth | relevance | operational stability
```

A conceptual quality model — **not initially reducible to one opaque score**.
Individual dimensions are measurable and independently inspectable.

Deferred to Phase 9.
