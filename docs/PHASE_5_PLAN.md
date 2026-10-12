# Phase 5 Plan — Single-Persona Broadcasting

**Spec sections:** §13 (Broadcast Director), §14 (LLM orchestration),
§15 (script construction and validation), §16 (TTS and audio)
**Exit criteria:** Gate 6 (approved script generation), Gate 7 (continuous audio)
**Status:** PLANNED

---

## 1. What this phase builds

The chain from an editorial decision to sound in a listener's ears:

```
ranked stories
    │
    ▼
[§14.4] context assembly ── targeted, versioned evidence packet
    │
    ▼
[§14.3] generation request ── idempotent, bounded, versioned
    │
    ▼
[§14.1] provider ── local-first, capability-declaring
    │
    ▼
[§15.1] broadcast script ── first-class persistent artifact
    │
    ▼
[§15.4] 8-stage validation ── independent, mostly deterministic
    │
    ▼
[§13.3] segment lifecycle ── 11 explicit states
    │
    ▼
[§16.2] TTS provider ── local-first, voice profiles
    │
    ▼
[§16.6] audio buffer ── ordered, bounded, stale-chunk rejecting
    │
    ▼
[§13.5] interrupt-safe delivery
```

## 2. Modules to build

### `core/broadcast/models.py` — domain entities

| Entity | Spec | Notes |
|---|---|---|
| `BroadcastSession` | §13.2 | Persisted state for crash recovery |
| `Segment` | §13.3 | 11-state lifecycle |
| `BroadcastScript` | §15.1 | Persistent, versioned, turns |
| `ScriptTurn` | §15.1 | Persona, speech act, claim refs |
| `GenerationRequest` | §14.3 | Idempotency key, evidence packet, versions |
| `EvidencePacket` | §14.3 | Application-built, never model-discovered |
| `VoiceProfile` | §16.3 | Separate from persona |
| `AudioChunk` | §16.5 | Segment + synthesis attempt bound |
| `AudioArtifact` | §16.7 | Retention policy, content hash |
| `Persona` | §12 | Single-persona in this phase |

Enums: `SegmentStatus` (11 states), `SpeechAct` (§15.1), `ValidationOutcome`
(§15.5, 5 outcomes), `ValidationStage` (§15.4, 8 stages), `EpistemicFunction`
(§15.2, 11 categories).

### `core/broadcast/providers.py` — LLM and TTS abstraction

`LanguageModelProvider` and `TTSProvider` protocols (§14.1, §16.2). A provider
**declares** its capabilities; the interface never assumes. A stub provider
implements both for tests, so no live model is needed for the gates.

**Critical:** §14.1 — "A provider must not claim support for a capability that
it cannot reliably deliver." `capabilities()` returns only what actually works.

### `core/broadcast/context.py` — context assembly (§14.4)

Builds the evidence packet from stored records. Includes contradictory evidence
(not only supporting), unresolved questions, required disclaimers, previously
broadcast statements. Preserves the distinction between source material,
system summaries, and persona interpretation.

### `core/broadcast/validation.py` — the 8 stages (§15.4)

Seven of eight stages are deterministic. Stage 5 (epistemic consistency) and
stage 3 (contradiction review) accept an optional verifier, but must not
require one — §15.6 says verification should use multiple failure-independent
mechanisms, and a second model response is not a guarantee of truth.

### `core/broadcast/director.py` — the director (§13)

Owns session state, segment lifecycle, the speech queue. Explicitly does **not**
own the world model or editorial policy (§13.1).

### `core/broadcast/audio.py` — buffer and chunking (§16.4–16.7)

Ordered, bounded, backpressure, stale-chunk rejection by segment+attempt.

### `core/broadcast/interrupt.py` — interruption policy (§11.6)

Three actions: `CONTINUE`, `QUEUE_NEXT`, `INTERRUPT`. Segment-boundary by
default. Records abandoned speech so it cannot replay.

## 3. What this phase deliberately does NOT build

| Deferred | Reason | Gate |
|---|---|---|
| Multi-persona dialogue | §12 is Phase 6 | 9 |
| Persistent TTS/LLM wiring to a real server | Needs a live model; gates must pass without one | 8 |
| Audio artifact files on disk | §16.7 is optional retention; metadata model is built now | 7 |
| Listener interaction | §17 | 10 |
| Exactly-once delivery | §13.6 explicitly does not assume it | 8 |

## 4. Test strategy

Every gate must pass with **stub providers** — no live model, no live TTS.
This is the same discipline as Phase 2's `StubTransport`.

Test classes and what they prove:

| Class | Count | Proves |
|---|---|---|
| `TestSegmentLifecycle` | ~18 | Illegal transitions rejected; TTS failure doesn't invalidate evidence |
| `TestScriptStructure` | ~10 | Script is persistent, versioned, turns carry claim refs |
| `TestEpistemicLabeling` | ~8 | Every substantive statement classified |
| `TestProvenance` | ~8 | A source list is not support; refs must resolve and relate |
| `TestValidationStages` | ~20 | Each stage independently catches its failure |
| `TestValidationOutcomes` | ~6 | Recoverable problems vs. unspeakable content |
| `TestIndependentVerification` | ~6 | Self-review is not accepted as verification |
| `TestGenerationContract` | ~12 | Idempotency, deadlines, retry records |
| `TestPromptInjection` | ~10 | Malicious article cannot redirect the task |
| `TestContextAssembly` | ~10 | Contradictory evidence included, not just supporting |
| `TestAudioBuffer` | ~14 | Ordering, bounds, cancellation, stale chunks |
| `TestInterruption` | ~10 | Abandoned audio never plays; history preserved |
| `TestDirectorState` | ~8 | Recovery after process termination |
| `TestPostBroadcastCorrection` | ~6 | Original transcript preserved (§15.7) |

Total: ~156 tests → ~638 suite.

## 5. Gates

### Gate 6 — approved script generation

1. A generation request produces a persistent, versioned `BroadcastScript`
2. Every substantive statement carries an epistemic function classification
3. A factual claim cannot be broadcast without a resolvable evidence reference
   **that actually supports it** — a metadata source list is not support (§15.3)
4. All 8 validation stages are defined, and each catches at least one failure
   class independently
5. `REJECTED` distinguishes unspeakable content from a recoverable generation
   problem (§15.5)
6. A malicious source document cannot redirect the task (§14.5)
7. An unsupported allegation does not pass because its prose is polished (§15.5)

### Gate 7 — continuous audio

1. Approved segments are synthesized in ordered chunks with sequence numbers
2. Chunks are bound to segment + synthesis attempt
3. A cancelled or superseded generation's chunks are never delivered (§13.5)
4. A slow client cannot consume unbounded memory (§16.6)
5. Interrupted speech is recorded and cannot replay (§13.5)
6. Generated ≠ validated ≠ submitted ≠ synthesized ≠ delivered ≠ consumed —
   all six are distinct events (§13.4)

## 6. Order of work

1. Models and enums
2. Providers (protocols + stub)
3. Context assembly
4. Generation request contract and idempotency
5. Script structure and epistemic labeling
6. Validation — stages 1, 2, 4, 6, 7, 8 (deterministic)
7. Validation — stages 3, 5 (with optional verifier, not required)
8. Independent verification
9. Segment lifecycle and director state
10. TTS provider and chunking
11. Audio buffer with stale-chunk rejection
12. Interruption policy
13. Post-broadcast correction
14. Gates 6 and 7 verification
15. Docs and commit

## 7. Known risks

**Prompt injection defense is the hardest part.** §14.5 says the application
"must not rely on prompt wording alone." With a stub provider the injection
tests can only prove the *structural* controls — identifier validation,
allowlisted actions, no credentials in context. They cannot prove a live model
resists manipulation. That limitation must be documented, not glossed.

**Validation stages 3 and 5 without a model.** Contradiction review against a
script needs the contradiction machinery from Phase 3, which is deterministic.
That covers quantity and temporal conflicts. A script that contradicts a
qualitative claim will pass stage 3 without a verifier. Honest gap.

**Exactly-once is out of scope by spec.** §13.6 says delivery must be
*recoverable* and duplicates *detectable*, not impossible. The chunk sequence
numbers support that; the guarantee itself is not claimed.
