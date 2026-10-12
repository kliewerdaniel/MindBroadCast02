# ADR Addendum 5 — Decisions Taken During Phase 5 Implementation

Companion to `DECISIONS_PHASE1.md` through `DECISIONS_PHASE4.md`.

---

## ADR-031: Seven of eight validation stages are deterministic; the verifier is optional

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §15.4 defines eight validation stages. §15.6 says the final step
"should not simply ask the same model that wrote the script whether its own
output is correct," and lists mechanisms including deterministic claim-reference
checks, rule-based temporal checks, a separate verifier prompt, and a different
model for high-impact segments.

**Decision:** stages 1, 2, 4, 6, 7, 8 are deterministic. Stages 3 and 5
(contradiction review, epistemic consistency) run deterministic checks and
**optionally** accept a verifier. No gate requires a live model.

**Rationale:** §15.6's "whenever practical" and "no individual technique is
sufficient" both point the same way — the deterministic stages are the floor
that always runs, and a verifier raises the ceiling. Making the verifier
mandatory would mean the gates never pass in CI, which is the same reasoning
that produced Phase 2's `StubTransport`.

**Accepted limitation, recorded not hidden:** without a verifier, stage 3
catches quantity, unit, and temporal contradictions (reusing Phase 3's
machinery) but not qualitative ones. A script contradicting a qualitative
claim passes stage 3. This is a real gap, not a claimed coverage.

---

## ADR-032: FATAL is distinct from ERROR in the outcome vocabulary

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §15.5 requires the system to "distinguish recoverable generation
problems from content that should not be broadcast," and states that "a segment
that makes an unsupported allegation should not automatically pass simply
because its prose is polished."

**Decision:** four severities — `INFO`, `WARNING`, `QUALIFICATION`, `ERROR`,
`FATAL`. The mapping: `FATAL` → `REJECTED` (terminal), `ERROR` →
`REQUIRES_REGENERATION`, `QUALIFICATION`/`WARNING` →
`APPROVED_WITH_QUALIFICATIONS`.

**Rationale:** a two-level severity would force a choice between letting an
unsupported allegation through as "needs a tweak" or rejecting a merely
malformed script as unspeakable. Both are wrong. The unsupported-allegation
case is tested directly, including with polished prose.

---

## ADR-033: A synthesis failure returns the segment to APPROVED, never to GENERATED

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §13.3 states that "a failed TTS request should not invalidate the
underlying evidence or necessarily require a new script. The segment can remain
approved while the media subsystem retries synthesis."

**Decision:** `record_synthesis_failure` moves `SYNTHESIZING → FAILED →
APPROVED`. The retry path is `APPROVED → SYNTHESIZING` with a new
`media_generation_id`. The script and its evidence references are untouched.

**Rationale:** routing a TTS failure back to GENERATED would discard a
validated script and re-invoke the model — expensive, and it would mean a
transient audio outage silently invalidated good editorial work. Tested:
`script_id` survives a synthesis failure.

---

## ADR-034: Stale-chunk rejection is enforced by the segment+attempt binding

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §13.5 requires that "when a segment is canceled or superseded,
chunks belonging to its obsolete generation must not be delivered as if they
were current." §16.4 step 6 requires associating each chunk with its segment
and sequence number.

**Decision:** every `AudioChunk` carries `segment_id` and `synthesis_attempt`.
`AudioBuffer.add_chunk` and `BroadcastDirector.attach_audio` both reject a
chunk whose `synthesis_attempt` differs from the segment's current
`media_generation_id`, raising `StaleChunk`/`InvalidState`.

**Rationale:** the binding is the only way to know a late-arriving chunk
belongs to an abandoned generation. Without it, a chunk from a superseded
synthesis could play after an interruption, which §13.5 explicitly forbids.
Tested: a chunk from `gen-1` is rejected when the segment is on `gen-2`.

---

## ADR-035: Interruption is segment-boundary-first, and abandoned audio is dropped from the delivery set

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §13.5 says the initial implementation "should favor
segment-boundary interruptions," and that for urgent cases the system "must
record any abandoned speech and prevent the abandoned audio from accidentally
playing after the interruption." §11.6 adds that an interruption "must not
discard the current segment's history."

**Decision:** `interrupt()` records an `Interruption` with
`stopped_at_sequence`, `completed_its_point`, `resume_later`, and the ids of
abandoned chunks, then removes the segment's audio from the delivery set. The
segment transitions to `INTERRUPTED`, not `CANCELLED`, so it can resume
explicitly via `resume_interrupted()`.

**Rationale:** dropping the audio from `self.audio` is what makes "cannot
accidentally play" structural rather than a filter someone might forget. Keeping
the interruption record (rather than just a flag) is what preserves history.
Tested: after interruption the audio is absent, `resume_later` survives, and
resume is an explicit call.

**Known limitation:** the interruption policy is a threshold function of
materiality/urgency/evidence/verification. It does not yet read the current
segment's actual playback progress against a real clock — `evaluate_interruption`
takes progress as an input but nothing supplies it yet. Phase 6 wiring.

---

## ADR-036: A correction appends; it never rewrites the transcript

**Status:** RATIFIED
**Date:** 2026-10-12

**Context:** §15.7 requires preserving "the original transcript and audio
history" and states a correction "should not merely replace the original
transcript with revised wording." The audience and operator must distinguish
what was originally said from what is now believed supported.

**Decision:** `TranscriptArchive` is append-only. `issue_correction` archives
the original if absent, then records the correction as a second entry linked
via `correction_of`, and marks the original `superseded_by`. No entry is ever
deleted or edited.

**Rationale:** an append-only archive makes §15.7 step 9 true by construction
rather than by discipline. Tested: after a correction the archive holds two
entries and the original text is byte-identical to what was recorded.

**Bug found and fixed while testing:** `issue_correction` originally recorded
only the correction, so a correction issued without a prior archive call lost
the original. Now it archives the original first when `original_script` is
supplied.
