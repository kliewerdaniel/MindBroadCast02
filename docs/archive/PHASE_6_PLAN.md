# Phase 6 Plan — Multiple Personas and Analytical Dialogue

**Spec sections:** §12 (persona architecture), §11.5 (transitions)
**Exit criteria:** Gate 9 (multi-persona coherent dialogue), plus the §12.5
disagreement model and §12.7 consistency evaluation
**Status:** PLANNED

---

## 1. What this phase builds

The machinery for several distinct analytical perspectives sharing one
evidence model:

```
shared evidence packet (§12.4 step 1)
    │
    ├─► persona A independent first-pass analysis  (§12.4 step 3)
    ├─► persona B independent first-pass analysis
    └─► persona C independent first-pass analysis
    │
    ▼
identify substantive disagreements (§12.4 step 5, §12.5)
    │
    ▼
structured exchange addressing them (§12.4 step 6)
    │
    ▼
final segment script (§12.4 step 7)
    │
    ▼
validate against the shared evidence packet (§12.4 step 8)
```

Plus persistent persona memory (§12.6) and consistency evaluation (§12.7).

## 2. Modules to build

### `core/personas/models.py` — persona domain

| Entity | Spec | Notes |
|---|---|---|
| `PersonaConfig` | §12.2 | Versioned; an update creates a version, never a silent rewrite |
| `AnalyticalPreferences` | §12.2 | analytical / epistemic / rhetorical / interaction / memory |
| `Disagreement` | §12.5 | The full field set, including `disagreement_type` |
| `PersonaMemoryEntry` | §12.6 | Typed, with provenance and validity status |
| `PersonaAnalysis` | §12.4 | A first-pass output with its assumptions recorded |
| `ConsistencyReport` | §12.7 | The six measured dimensions |

Enums: `DisagreementType` (7 types), `MemoryType` (5 types per §12.6),
`ResolutionStatus`.

### `core/personas/memory.py` — bounded persistent memory (§12.6)

Five memory types, each with provenance and a validity status. **Bounded** —
"memory must not become an uncontrolled transcript appended to every prompt."
A retrieval method caps what enters context.

### `core/personas/analysis.py` — independent first-pass (§12.4)

Each persona analyzes independently. The orchestrator enforces the ordering:
all first passes complete before any persona sees another's output. This is
the structural guard against §12.4's warning that "later personas inherit the
assumptions and framing of earlier responses."

### `core/personas/disagreement.py` — the disagreement model (§12.5)

Extracts the precise disputed proposition. Refuses to manufacture disagreement
when evidence supports a straightforward conclusion, and refuses to force
consensus when the issue is unresolved. Both are tested.

### `core/personas/dialogue.py` — structured exchange (§12.4 step 6)

Composes a multi-persona exchange from recorded disagreements. The exchange is
built from the structured disagreement records, not by re-prompting personas
until they "disagree."

### `core/personas/consistency.py` — §12.7 evaluation

Six dimensions. The key distinction: a persona changing its mind after
incorrect predictions is legitimate; a persona changing outlook at random is a
consistency problem. The evaluation distinguishes them by whether the change is
explainable through new evidence or a config update.

### `core/personas/imitation.py` — §12.7's "unintentional imitation"

Measures whether two personas' outputs have become indistinguishable. §14.7's
correlated-error concern in concrete form: if every persona uses the same model
with the same framing, they may confidently reproduce the same mistake.

## 3. What this phase deliberately does NOT build

| Deferred | Reason |
|---|---|
| Real multi-voice TTS routing | §12.1 mentions speaking voices; the voice-profile model exists from Phase 5. Wiring multiple live voices is a Phase 8 concern |
| Audience interaction | §17 is Phase 7 |
| A real model behind personas | Gates must pass with stubs, per the Phase 2 discipline |

## 4. Test strategy

| Class | Count | Proves |
|---|---|---|
| `TestPersonaConfig` | ~10 | Versioned; update creates a version, never a silent rewrite |
| `TestEpistemologyPersonalitySeparation` | ~8 | Personality never determines factual truth (§12.3) |
| `TestIndependentAnalysis` | ~12 | First passes isolated; no persona sees another before all complete |
| `TestAssumptionRecording` | ~6 | Each analysis records its assumptions and evidence |
| `TestDisagreementModel` | ~14 | Precise proposition; 7 types; no manufacture, no forced consensus |
| `TestSharedFacts` | ~6 | Disagreement records what is agreed, not only what is disputed |
| `TestPersonaMemory` | ~16 | 5 types, bounded, provenance, validity |
| `TestMemoryRevision` | ~6 | A persona remembers a prior position and its weakening (§12.6) |
| `TestConsistency` | ~14 | Six dimensions; explainable change vs random drift |
| `TestImitation` | ~8 | Unintentional imitation detected (§12.7, §14.7) |
| `TestDialogueComposition` | ~10 | Exchange built from disagreement records |
| `TestSharedEvidenceBaseline` | ~8 | All personas draw on the same evidence packet |

Total: ~118 tests → ~751 suite.

## 5. Gate 9 criteria

1. Multiple personas participate in one segment, each with a distinct configured role
2. Initial analyses are generated **independently** — no persona sees another's
   first pass before all first passes complete (§12.4)
3. Each analysis records the assumptions and evidence it used (§12.4 step 4)
4. A disagreement identifies the **precise proposition** under dispute (§12.5)
5. A disagreement records shared facts alongside disputed assumptions (§12.5)
6. The system does **not** manufacture disagreement when evidence supports a
   straightforward conclusion (§12.5)
7. The system does **not** force consensus when the issue is unresolved (§12.5)
8. A structured exchange addresses the recorded disagreements (§12.4 step 6)
9. The final script validates against the shared evidence packet (§12.4 step 8)
10. Persona memory is persistent, bounded, typed, and provenance-carrying (§12.6)
11. A persona can recall a prior position and its subsequent weakening (§12.6)
12. Consistency evaluation distinguishes explainable change from random drift (§12.7)
13. Personality does not determine factual truth; the shared evidence model does (§12.3)
14. Unintentional imitation between personas is detectable (§12.7, §14.7)

## 6. Order of work

1. Persona config (versioned)
2. Memory subsystem (5 types, bounded)
3. Independent first-pass analysis + assumption recording
4. Disagreement extraction
5. Shared-facts tracking
6. Structured exchange composition
7. Consistency evaluation
8. Imitation detection
9. Dialogue orchestrator wiring all of the above
10. Gate 9 verification
11. Docs and commit

## 7. Known risks

**Correlated errors are not solved, only made visible.** §12.4 says independent
first-pass analysis "does not eliminate correlated errors, especially when all
personas use the same underlying model. That limitation should be documented."
§14.7 says the same. The imitation detector surfaces the symptom; it does not
fix the cause. The fix would be genuinely independent models, which is an
infrastructure decision outside this phase.

**Disagreement extraction without a model.** With stubs, the disagreement model
can be built and tested structurally — the field set, the types, the
no-manufacture rule — but extracting a *substantive* disagreement from prose
needs reasoning. The deterministic path (comparing recorded positions on the
same proposition) is what's tested. Semantic extraction is the stub boundary.

**"Bounded" memory needs a number.** §12.6 says bounded, not "small." The
implementation picks concrete caps and documents them as tunable, not
principled.
