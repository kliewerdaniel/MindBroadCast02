# Open Questions

Source: spec §27, §29. Each has a recommended default so work is never blocked,
but each is a real decision that should be ratified in DECISIONS.md before the
code that depends on it.

## Blocking Phase 1

### OQ-1: Port-and-fix or rebuild? (→ ADR-002)

**Recommendation:** port and fix, contingent on reading
`core/persistence.py` in full. If transaction handling and connection
centralization are unsound, rebuild the persistence layer while keeping the
tests as the specification.

**Decide before:** any Phase 1 code.

### OQ-2: Article versioning — separate table or `supersedes_article_id`?

Spec §5.3.1 explicitly leaves this open ("The invariant is more important than
the choice of table").

**Recommendation:** `supersedes_article_id` self-FK on the `articles` table.
Fewer tables, one query pattern, and the invariant (a material revision must not
erase the historical version) is enforced by append-only semantics rather than by
a second table's discipline. Revisit if version queries become hot paths.

**Decide before:** B2 (Article model).

### OQ-3: Where does `WorldSnapshot` serialization live?

Spec §5.9: "For a small deployment, a snapshot may contain a canonical
serialized representation... For a larger deployment, the system may store a
compact snapshot plus a reference to the immutable event sequence."

**Recommendation:** full serialization stored as an artifact with a DB reference,
following the artifact lifecycle (temp → hash → move → commit). This is a small
deployment; the event-sequence reconstruction path can be added later without
changing the snapshot contract, as long as `included_record_manifest` is present
from the start.

**Decide before:** D1 (snapshot rebuild).

## Blocking Phase 2

### OQ-4: Which RSS/Atom feeds form the initial corpus?

The prior repo has `feeds.yaml`. Needs review for: publisher diversity,
ownership-group coverage (to exercise the independence logic), geographic
spread, and at least one feed known to produce corrections.

**Recommendation:** 8–15 feeds spanning at least 4 ownership groups, plus one
official-source endpoint. Diversity is a test fixture requirement, not a
preference — invariant I1 and I8 are untestable without it.

### OQ-5: Content retention policy for raw captures?

Spec §5.3: "Content retention must respect configured storage limits and
applicable source-access restrictions."

**Recommendation:** retain normalized text indefinitely (small), retain raw HTML
for 30 days by default, configurable per source. Note in config that this is a
**policy declaration** — the retention job is what enforces it (invariant I16).

### OQ-6: Near-duplicate threshold?

Spec §7.5.1 requires a near-duplicate comparison stage but sets no threshold.

**Recommendation:** start with a high threshold (conservative — prefer
associating with an existing story over discarding), because a near-duplicate
may carry a correction (invariant I9). Discarding is the destructive action;
associating is recoverable. Measure on the evaluation corpus before tuning.

## Blocking Phase 3

### OQ-7: Which local extraction model?

Spec §29: "Provider abstraction with local inference as the default." The user
owns local inference (Ollama/llama-server).

**Recommendation:** route extraction tasks to a small structured-output model
(entity extraction, claim extraction) behind the provider interface. Do not
hard-code a model name in domain logic — the routing table in ARCHITECTURE.md
§Model routing is the contract.

### OQ-8: CKU as a real table or a view/interface?

Spec §8.3: "The safest initial design is to treat CKUs as a stable interface
over existing domain objects, rather than introducing a second competing system
of record."

**Recommendation:** interface/query layer over Claim + Evidence, no separate
table. Revisit only if CKU-level provenance queries become a bottleneck.

## Blocking Phase 5

### OQ-9: TTS provider default (→ ADR-008)

**Recommendation:** Edge-TTS behind the interface for Phase 5 speed, with an
explicit `local` provider slot. Ratify a genuinely local engine before Phase 8,
since Gate 7 requires TTS failure recovery and a network dependency weakens it.

The user has Qwen3-TTS on MLX locally (per project history) — a strong candidate
for the `local` slot.

### OQ-10: Audio transport format and framing?

Spec §19.4: "The actual transport may use chunked HTTP audio, a
browser-compatible streaming format, or an optional external radio server."

**Recommendation:** chunked HTTP with an explicit length-prefixed framing
protocol; SSE reserved for metadata only (spec §16.8). Browser-native formats
avoid a decode dependency but constrain format normalization; chunked raw PCM or
Opus behind a defined framing gives control over stale-chunk rejection, which
Gate 7 requires.

### OQ-11: Turn duration calibration source?

Spec §11.4 gives `D = 60W/R` as a rough estimator that "should be calibrated
against actual TTS output."

**Recommendation:** ship the estimator with R=150 wpm default, then calibrate
per voice profile from real synthesis output during Phase 5. Store the
calibration on the VoiceProfile, not globally.

## Blocking Phase 4

### OQ-12: Initial ranking weights?

Spec §10.3: "The weights are tunable parameters, not universal constants."

**Recommendation:** start with equal weights, record every ranking decision with
its factor contributions, and tune from the replay evaluation (spec §22.7) —
not from intuition. The decision record is what makes tuning defensible.

### OQ-13: Editorial policy versioning?

Spec §20.6: "These policies should be versioned and tested like other system
components."

**Recommendation:** editorial policy lives in versioned YAML (like sources,
ADR-007), with the version stamped on every BroadcastPlan and selection record.
Policy changes are config changes, not code changes.

## Deferred (Phases 6–9)

### OQ-14: Persona cast?

Spec §12.1 gives illustrative roles (primary presenter, analytical, skeptical,
historical, audience-facing) but "the system should allow the user to define
arbitrary personas without requiring a fixed cast."

**Recommendation:** ship three (presenter, analytical, skeptical) and let the
cast be data. **Note the Chris constraint:** if any persona relates to the
user's late brother, it is a tagged reconstruction model, never canonical, and
no fabricated biographical detail may be presented as fact.

### OQ-15: Local vs remote verification model for high-impact segments?

Spec §15.6: "A different model for high-impact segments."

**Recommendation:** defer to Phase 8. Initially, a separate verifier **prompt**
on the same local model plus deterministic checks. A second local model is a
resource decision, not an architectural one.

### OQ-16: Improvement-loop approval authority?

Spec §28.2: low-risk reversible parameters may be adjusted automatically;
higher-impact changes require stronger evidence and explicit approval.

**Recommendation:** automatic adjustment limited to polling intervals, background
task order, and cache policy. Everything else proposes, a human approves. The
system must never autonomously weaken factual validation.
