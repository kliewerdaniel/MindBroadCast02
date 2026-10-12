# Project Brief — MindBroadcast

Source: spec §1, §2, §27, §29, §30.

## What it is

MindBroadcast is a **persistent, autonomous broadcasting system** that
continuously transforms incoming information about the world into an evolving
stream of spoken analysis, commentary, debate, explanation, and entertainment.

It is not a podcast generator that renders a fixed script. It is a
**continuously running information system** that maintains an internal
representation of the world, ingests new information, tracks the evolution of
stories, evaluates competing claims, identifies meaningful changes, and decides
what to communicate next.

A listener should be able to open it at any moment and find a coherent
broadcast already in progress — one that remembers what it previously reported,
knows which questions remain unresolved, separates new developments from
repeated coverage, and adapts to its audience.

## The defining property

The system separates four things that a naive pipeline collapses:

1. **What the system knows** (world model: observations, claims, evidence).
2. **What it believes may be happening** (analytical interpretation).
3. **What its individual personas think** (perspective).
4. **What the broadcaster actually says** (published speech).

A news article is not an event. An event is not a claim. A claim is not
evidence. Evidence does not automatically make a claim true. An interpretation
is not a fact. A persona's opinion is not a verified conclusion. A generated
sentence is not broadcastable merely because a model produced it.

The central principle (§1):

> The world changes, the system observes those changes, its internal model is
> updated, its perspectives interpret the changes, and its broadcast
> communicates those interpretations **without erasing the distinction between
> evidence and opinion.**

## Primary objectives (spec §1.1)

The system MUST:

1. Operate continuously without a human initiating every segment.
2. Ingest from multiple independently configurable sources.
3. Persist source material, extracted information, relationships, corrections,
   and historical state.
4. Distinguish articles, claims, events, entities, evidence, interpretations,
   and corrections.
5. Maintain an evolving world model rather than treating each generation as an
   isolated task.
6. Detect meaningful developments in ongoing stories.
7. Identify novelty, uncertainty, conflicting accounts, unresolved questions.
8. Support multiple persistent personas with distinct editorial characteristics.
9. Generate speech combining reporting, explanation, analysis, debate, entertainment.
10. Associate broadcast statements with supporting evidence and generation history.
11. Never present unsupported speculation as verified reporting.
12. Accept listener interaction without destabilizing the broadcast pipeline.
13. Generate audio continuously with text/speech/state synchronization.
14. Recover from subsystem failures without terminating the broadcast.
15. Default to local inference and local storage.
16. Expose internal state, reasoning artifacts, and current broadcast to an operator.
17. Retain enough history to avoid re-reporting old developments as new.
18. Support deterministic tests for infrastructure and reproducible evaluations
    for probabilistic components.

## Non-goals (spec §1.2)

MindBroadcast is NOT:

- A general-purpose conversational assistant with a news-themed interface.
- A single-prompt article summarizer.
- A system that assumes every ingested article is accurate.
- A fully autonomous publisher whose claims bypass validation.
- A replacement for primary sources or professional journalism.
- A system that requires a cloud LLM to remain operational.
- A collection of personas sharing no common information model.
- A rigid playlist generator for prerecorded segments.
- An agent that freely modifies its own source code or policies.
- A system that equates fluent speech with factual correctness.

Autonomy here means **decision-making through explicit interfaces, bounded
execution permissions, and verifiable state transitions** — not unrestricted
authority over storage, network, or persistent state.

## The control loop (spec §2.1)

```
Observe → Normalize → Understand → Evaluate → Select →
Compose → Validate → Speak → Measure → Update → (repeat)
```

Conceptual, not a synchronous pipeline. Ingestion, knowledge processing,
editorial planning, generation, and audio delivery run as separate coordinated
subsystems: a new article must not wait for the current audio segment; a slow
LLM call must not block the transcript or listener input.

## Programming modes — what "infinite" means (spec §2.2)

An infinite broadcast does not require infinite new articles. It requires a
meaningful program when the external environment is quiet. Modes:

| Mode | Behavior |
|---|---|
| Breaking developments | New events / material changes get priority when important and well-supported. |
| Continuing coverage | Revisit ongoing events on new evidence, consequences, decisions. |
| Analysis | Explore causes, incentives, institutions, history, consequences. |
| Synthesis | Relate separate developments; must be labeled interpretation unless the connection is established. |
| Unresolved questions | Discuss what is known, unknown, and what would resolve it. |
| Historical context | Explain how the current situation developed. |
| Audience-directed | Listener questions influence topic selection without overriding factual/safety constraints. |
| Low-volume | Clearly identified background analysis, retrospectives, established evidence. |

**The system must never manufacture a breaking event merely to fill silence.**

## Operating modes (spec §2.3)

Distinct from editorial mode. The system can be `LIVE` while performing
historical analysis.

`LIVE`, `IDLE`, `DEGRADED`, `REPLAY`, `SIMULATION`, `MAINTENANCE`, `STOPPING`,
`STOPPED` — full transition table in STATE_MACHINES.md.

## Architectural orientation (spec §29 defaults)

| Decision | Default |
|---|---|
| Deployment | Local-first application, binds loopback by default. |
| Backend | Python, async. |
| Database | SQLite with WAL + explicit transactions. |
| API | FastAPI or equivalent async Python framework. |
| Ingestion | RSS/Atom first, then selected structured APIs. |
| Search | SQLite FTS initially; local embeddings behind a replaceable index interface. |
| Knowledge graph | Relational tables with explicit relationships initially. |
| Language models | Provider abstraction, local inference default. |
| TTS | Local synthesis default. |
| Broadcast structure | Short turns, 10–40s typical. |
| Personas | One reliable presenter first, then multiple analytical perspectives. |
| Audio delivery | Browser-based live stream; Icecast optional later. |
| Audience interaction | Asynchronous, persistent, subordinate to editorial controls. |
| Corrections | First-class records linked to original claims and broadcasts. |
| Human oversight | Configurable review for high-impact or uncertain content. |

## Implementation philosophy

**Deterministic infrastructure surrounding probabilistic intelligence.**

> Models propose. Schemas constrain. Services authorize. Transactions commit.
> Evidence records what happened. (spec §3.2)

## Definition of "complete"

The project is complete when it functions as a **persistent broadcasting system
whose information, decisions, and outputs remain understandable and recoverable
over time** — not when it produces a convincing demo of an AI presenter reading
a script. Full criteria: ACCEPTANCE_CRITERIA.md.
