# Phase 7 Plan — Live Audience and Operator Experience

**Spec sections:** §17 (audience interaction), §18 (UI/operator), §19 (API/events),
§20.2–20.4 (rate limiting, abuse, listener input)
**Exit criteria:** Gate 8 (operator observability and control)
**Status:** PLANNED

---

## 1. What this phase builds

Three subsystems that make the engine observable, controllable, and interactive:

```
┌─ §19 API layer ──────────── domain operations, not raw tables
│   ├── /broadcast/*          start/stop/session/segments/transcript
│   ├── /stories/*  /claims/* world-model queries
│   ├── /sources/*            admin: register, enable, update
│   ├── /listener/messages    submit (rate-limited)
│   ├── /events               event timeline
│   └── /metrics  /health     status
│
├─ §19.3 SSE event stream ─── live metadata, not audio
│   └── broadcast.state_changed, transcript.turn_added, ...
│
├─ §17 audience channel ───── durable input, rate-limited, abuse-classified
│   └── message -> queue -> (maybe) response segment
│
└─ §18 operator console data ─ the four questions:
      1. What is it saying now?      -> player/transcript
      2. Why?                         -> SHOW ANALYSIS
      3. What is it observing?        -> world activity
      4. What's next?                 -> scheduled topics
```

## 2. Modules to build

### `core/api/contracts.py` — request/response schemas (§19.5)

Versioned, stable representations. §19.5: "Internal domain models should not
automatically become public API schemas." So the API layer defines its own
response types that evolve independently of the database. Structured errors with
error code, message, request id, retryability, field details.

### `core/api/router.py` — endpoint dispatch (§19.2)

A framework-agnostic router. §19.1: "the architecture should not depend on a
specific web framework." Each endpoint is a handler taking a parsed request and
returning a response envelope. A FastAPI adapter is built but the router core is
plain.

### `core/api/server.py` — FastAPI adapter

Wires the router to FastAPI if available; the core has no framework dependency.

### `core/audience/messages.py` — listener input (§17.1–17.3)

The 10-step processing pipeline. **The durable-input rule**: an incoming
message "should not synchronously block article ingestion, knowledge
compilation, or audio delivery." It becomes a durable event the planner
evaluates.

### `core/audience/moderation.py` — abuse protection (§17.5, §20.3–20.4)

Per-client rate limits, bounded length, duplicate detection, queue quotas,
abuse classification. The load-bearing rule: "A listener request may create a
research task. It must not directly establish a claim in the world model."

### `core/audience/feedback.py` — noisy signals (§17.4)

Signals recorded but never interpreted as measures of truth, importance, or
consensus. A replay may be confusion; a skip may be a technical problem.

### `core/api/events.py` — SSE event stream (§19.3)

Stable event ids, timestamps, types, versioned payloads. Reconnection and
replay support. §19.3: "The application should not rely on an in-memory event
queue as the only record of important state transitions" — so events persist.

### `core/api/show_analysis.py` — the audit surface (§18.4)

The most important interface. Exposes editorial objective, story context,
evidence, contradictions, interpretation, persona roles, unresolved questions,
selection factors, generation provenance, validation state. **Never** presents
private chain-of-thought as explanation.

### `core/api/transcript.py` — live transcript (§18.3)

Each entry carries speaker, segment, text, timestamps, playback state, story
refs, evidence links, epistemic classification, correction status, and whether
the segment completed/was interrupted/abandoned. Distinguishes generated text
from delivered text.

### `core/api/operator.py` — operator controls (§18.6)

Start/stop/pause/mute/skip/topic/persona-config/source-toggle/priority/
failed-tasks/corrections/refresh/export/usage/live-vs-sim. Actions affecting
persistent policy get explicit confirmation and audit records.

## 3. What this phase deliberately does NOT build

| Deferred | Reason |
|---|---|
| The browser frontend itself | This phase builds the API and data contracts the console consumes; a React/HTML frontend is a separate deliverable. The `pipeline-web-ui` skill exists for that. |
| Real SSE over a socket | The event model and stream semantics are built and tested; the transport adapter is thin. |
| Persistent listener accounts | §17.2: session identifiers, no accounts. |
| Audio transport over HTTP | §19.4 endpoint is declared; the audio buffer from Phase 5 is the source. Binary audio streaming is Phase 8. |

## 4. Test strategy

| Class | Count | Proves |
|---|---|---|
| `TestApiContracts` | ~10 | Versioned, stable, structured errors, no raw tables |
| `TestEndpointRouting` | ~12 | Each endpoint, 404/405, boundary validation |
| `TestListenerInput` | ~12 | Durable event, non-blocking, 10-step pipeline |
| `TestRateLimiting` | ~10 | Per-client limits, queue quotas |
| `TestAbuseProtection` | ~12 | Spam, duplicate, coordinated, false-claim induction |
| `TestListenerCannotEstablishClaims` | ~6 | §17.5's load-bearing rule |
| `TestFeedbackSignals` | ~8 | Recorded as noisy, never as truth/consensus |
| `TestEventStream` | ~12 | Stable ids, replay, reconnect, persisted |
| `TestShowAnalysis` | ~14 | All ten fields, no chain-of-thought leakage |
| `TestTranscript` | ~10 | Generated ≠ delivered; interruption recorded |
| `TestOperatorControls` | ~16 | Every control; confirmation + audit on persistent actions |
| `TestAccessibility` | ~8 | Not color-only; keyboard; transcript without audio |
| `TestFourQuestions` | ~6 | The §18.1 dashboard contract |

Total: ~136 tests → ~848 suite.

## 5. Gate 8 criteria

1. Every §19.2 endpoint is implemented and returns domain operations, not raw tables
2. A schema change cannot silently break clients — versioned, structured errors (§19.5)
3. The API does not expose raw database access or unrestricted admin to an unauthenticated client (§19.2)
4. A listener message becomes a durable input event and does **not** synchronously block ingestion/compilation/audio (§17.1)
5. A listener question is not automatically answered on air (§17.3)
6. A listener request can create a research task but cannot directly establish a claim (§17.5)
7. Per-client rate limits and queue quotas bound influence (§17.5)
8. Abuse classification catches spam, duplicates, and coordinated manipulation (§17.5)
9. Feedback signals are recorded as noisy evidence, never as truth or consensus (§17.4)
10. The SSE stream has stable ids, supports replay, and persists important transitions (§19.3)
11. SHOW ANALYSIS exposes all ten fields for the current segment (§18.4)
12. SHOW ANALYSIS does not present private chain-of-thought as explanation (§18.4)
13. The transcript distinguishes generated text from delivered text (§18.3)
14. An interrupted/abandoned segment is marked, not silently completed (§18.3)
15. Operator controls cover the §18.6 list; persistent actions get confirmation + audit
16. The dashboard answers the four §18.1 questions
17. Status is not communicated by color alone (§18.7)

## 6. Order of work

1. API contracts and structured errors
2. Router and endpoint dispatch
3. Event stream with persistence and replay
4. Listener message model and pipeline
5. Rate limiting and abuse classification
6. Feedback signals
7. SHOW ANALYSIS assembly
8. Transcript with generated/delivered distinction
9. Operator controls with confirmation/audit
10. World-explorer query surface
11. Accessibility data model
12. FastAPI adapter (thin)
13. Gate 8 verification
14. Docs and commit

## 7. Known risks

**The frontend is not built.** This phase delivers the API and data contracts;
a real browser console is a separate deliverable. The gate is verified against
the API layer with test clients, not a live browser. §18.7 accessibility is
enforced at the data-contract level (status fields are not color-encoded, so a
renderer cannot rely on color), not by pixel inspection.

**SSE replay over a real socket is untested.** The event store, id stability,
and replay logic are tested in-process. Transport-level reconnection is thin.

**Abuse classification is heuristic.** Coordinated manipulation detection is
pattern-based. It will miss sophisticated coordination; it catches the obvious
cases. Recorded as a floor, not a solution.
