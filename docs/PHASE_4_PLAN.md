# Phase 4 Plan — World Model and Story Intelligence

**Objective:** Maintain an evolving view of developments rather than a collection
of unrelated articles. (Spec §25 Phase 4, §9, §10)
**Gates 4 and 5:** World snapshots preserve consistent historical state; story
selection is explainable and reproducible.

**Entry state:** Phases 0–3 complete, Gates 1–3 MET — 428 tests passing.

---

## The central requirement

§9.1: "A database containing thousands of articles is not automatically a model
of the world." The world model must represent **relationships, changes,
unresolved questions, and the historical evolution of information** — and
§9.1 is explicit that the current view and the historical view "should not be
implemented as one undifferentiated state object."

§9.2 draws the line this phase exists to enforce:

> The system must distinguish the arrival of new content from the arrival of
> new information.

A new article is not a material change. Three more articles repeating one wire
report is not a development. This distinction is what stops the broadcaster
from repeatedly announcing the same thing — the failure mode §9.2 names
directly.

## Nine ranking factors, all operationally defined

§10.1 gives the objective as a formula with nine conceptual variables and then
says they "must be operationally defined before being combined into a
production ranking." That sentence is the acceptance criterion for this phase.
Each factor needs a computable definition, a stated input, and a recorded
value per decision — not a plausible-looking number.

| Factor | § | Operational definition |
|---|---|---|
| `I` Importance | 10.2 | Weighted from people-affected, economic scale, irreversibility, safety. **Not** attention volume. |
| `N` Novelty | 10.3 | Count of new claims/events/evidence/relationships/corrections since last coverage, weighted |
| `V` Temporal velocity | 10.4 | Σ W(d)·exp(−(t−t_d)/τ) over **material** developments, not articles |
| `E` Evidence quality | 10.6 | Primary vs secondary vs attributed; **ownership-group-aware independence** |
| `R` Relationship | 10.5 | Normalized centrality; contextual, never decisive |
| `U` Unresolved questions | 10.8 | Count × materiality of unanswered questions |
| `A` Audience relevance | 10.7 | **Bounded**, cannot override `I` or `E` |
| `C` Coverage continuity | 10.8 | Value of continuing vs switching |
| `D` Redundancy penalty | 10.9 | Coverage saturation |

## The four requirements most likely to be got wrong

### R1. Novelty is measured against the world model, not article text (§10.3)

> Novelty measures how much the newly available information changes the
> system's current understanding. It should be calculated against the existing
> world model rather than against article text alone.

So `N_s` is a diff: what claims, events, evidence, relationships, and
corrections appeared for this story since it was last covered. A tenth article
restating a known claim contributes zero novelty. Test: re-ingesting identical
content produces novelty 0.

### R2. Temporal velocity uses material changes, not article count (§10.4)

> Velocity must be measured using meaningful changes rather than article
> volume alone. The system should also distinguish a surge in reporting caused
> by new evidence from a surge caused by repeated publication of the same
> claim.

Test: ten duplicate articles and one genuine correction produce different
velocity despite identical article counts.

### R3. A late discovery can have high novelty (§9.3)

> An investigation published today may establish details about an incident
> that occurred months ago. The system should not necessarily rank this
> information as old merely because the underlying event is old.

So velocity and novelty use **observation time** (`first_observed_at`), not
`published_at`, for the recency term. Test: an article published today about a
year-old event is not ranked as stale.

### R4. Coverage saturation must not permanently suppress a story (§10.9)

> Saturation must not permanently suppress a story. New evidence or a material
> change should reduce the saturation penalty.

Test: a saturated story's eligibility rises again when a material change
arrives.

---

## Work streams

### A — Material change detection (`changes.py`)

Classify what actually changed, by type and significance, per §9.2's list of
ten change kinds. This is the input to novelty and velocity, so it must be
right before either is computed.

**Tests:** each of the ten change kinds detected; repeated content produces no
change; significance is recorded.

### B — Story engine (`stories.py`)

Closes Pass 5 and 6 properly: associates claims, evidence, events, and
entities into stories, and clusters articles describing one development.
Deterministic clustering on canonical identity (reusing the CKU identity from
Phase 3) plus entity overlap — no embeddings.

**Tests:** several articles about one development cluster into one story;
unrelated articles do not; a story tracks its unresolved questions.

### C — Ranking (`ranking.py`)

The nine factors, each a pure function of stored state. `P(s,t) = f(I,N,V,E,R,
U,A,C,D)` with the redundancy and transition penalties from §10.10.

**Tests:** every factor computable from stored records; a material correction
raises eligibility without a live model (spec §22.2 requires exactly this);
audience relevance is bounded and cannot override importance; equal-weight
defaults are recorded.

### D — Explainable selection (`selection.py`)

§10.11: every decision carries a recorded rationale derived from **structured
decision features**, not prose. The record is the source of truth; a
human-readable explanation is derived from it, never a substitute.

**Tests:** a selection record contains factor values and reasons; the
explanation is generated *from* the record.

### E — Coverage debt and saturation (`coverage.py`)

§10.8 and §10.9, including the non-suppression requirement (R4).

**Tests:** a major story with thin coverage accrues debt; a story covered ten
times without new information saturates; a material change lifts saturation.

### F — Historical replay (`replay.py`)

§9.1's historical view and §22.7's temporal-leakage requirement: at each time
point the system receives **only information available by then**.

**Tests:** replaying to time T shows only records first-observed ≤ T; a
snapshot taken at T does not imply knowledge of a later article.

### G — Fairness monitoring (`fairness.py`)

§10.12's nine monitored patterns. These "do not mechanically establish
editorial fairness — they reveal patterns that require inspection," so the
module *measures and reports*, it does not adjudicate.

**Tests:** source concentration detected; ownership-group concentration
distinguished from raw outlet count; a popularity-feedback loop is visible.

---

## Exit criteria — Gates 4 and 5

**Gate 4 (snapshots preserve consistent historical state):**

- [ ] A snapshot at time T reconstructs exactly what was known at T.
- [ ] Valid, observation, and transaction times remain distinct through replay.
- [ ] Replay to T never includes information first observed after T.
- [ ] A change to one claim does not invalidate every story summary (§9.5).

**Gate 5 (story selection is explainable and reproducible):**

- [ ] All nine ranking factors are operationally defined and computable.
- [ ] Novelty is measured against the world model, not article text.
- [ ] Temporal velocity uses material changes, not article volume.
- [ ] A late discovery is not ranked as stale.
- [ ] Coverage saturation lifts on a material change.
- [ ] Every selection carries a recorded rationale with factor values.
- [ ] Audience relevance is bounded and cannot override importance.
- [ ] The same stored state reproduces the same ranking (deterministic).
- [ ] Full suite green.
