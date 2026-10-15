# MindBroadcast

<p align="center">
  <strong>A persistent, autonomous, news-driven intelligence and broadcasting system.</strong>
</p>

<p align="center">
  Watches live news from around the world &middot; builds a durable model of it &middot; decides what is worth saying &middot; and speaks it &mdash; on its own, continuously.
</p>

<p align="center">
  <img alt="tests" src="https://img.shields.io/badge/tests-959%20passed-brightgreen">
  <img alt="phases" src="https://img.shields.io/badge/phases-0%E2%80%938%20complete-blue">
  <img alt="gates" src="https://img.shields.io/badge/gates-1%E2%80%9310%20MET-blue">
  <img alt="python" src="https://img.shields.io/badge/python-3.9%2B-blue">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-lightgrey">
</p>

---

## What this is

MindBroadcast is not a podcast generator that renders a fixed script. It is a
continuously running information system.

It ingests live news feeds from **25 sources across 8 world regions**, compiles
them into claims with verifiable provenance, clusters those claims into stories,
ranks them by real signal, and broadcasts the most newsworthy one — then sleeps
and does it again. It maintains an internal model of the world, tracks story
evolution, detects material change, evaluates competing claims, and decides
what to communicate next.

The system runs itself. Start it, and it watches, decides, speaks, and moves on
— going silent when there is nothing genuinely new, rather than re-airing.

> **The world changes, the system observes those changes, its internal model is
> updated, its perspectives interpret the changes, and its broadcast communicates
> those interpretations without erasing the distinction between evidence and
> opinion.**

> **Models propose. Schemas constrain. Services authorize. Transactions commit.
> Evidence records what happened.**

---

## Highlights

- **Autonomous station** — watches live RSS, ingests, compiles, clusters,
  ranks, and airs, continuously. Survives restart without re-airing.
- **World coverage** — 25 live feeds across North America, Europe, the Middle
  East, Asia, Africa, Latin America, South Asia, and Oceania.
- **Real local inference** — Qwen3 (`30b-a3b` MoE) for reasoning,
  `nomic-embed-text` for embeddings, Qwen3-TTS for speech. Localhost-only and
  fail-closed.
- **Multi-agent composition** — three personas (analyst / skeptic / historian)
  read the same grounded facts independently through distinct lenses; real
  disagreement is aired, manufactured debate is refused.
- **A deterministic grounding floor** — every composed turn is checked against
  source facts. Invented entities or figures are dropped, not aired.
- **One consistent voice** — the whole station speaks in a cloned voice
  (`chris.wav`), differentiated only by delivery.
- **Browser console** — live SSE transcript, per-segment audio playback,
  start/stop the station.

---

## Quick start

### 1. Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -m pytest -q          # 959 passing
```

### 2. Local inference

The broadcast needs a local LLM and a TTS server. Both are **localhost-only**
and fail closed — the system refuses to call a non-local endpoint unless
`SOVEREIGN_ALLOW_EXTERNAL_INFERENCE=1` is explicitly set.

```bash
# LLM (Ollama). The 30b MoE model is the default: 30B knowledge, ~3B active
# per token, so it runs fast while being far more coherent than smaller models.
ollama pull qwen3:30b-a3b
ollama pull nomic-embed-text
ollama serve

# TTS: the persistent Qwen3-TTS server (needs mlx_audio; verified on Python 3.14)
/opt/homebrew/bin/python3.14 -m core.inference.tts_server --port 7861
```

> **Note:** MindBroadcast never starts or stops your model server. It reports a
> dead endpoint and waits.

### 3. Run the console

```bash
python3 -m uvicorn core.inference.console:app --host 127.0.0.1 --port 7862
```

Open **http://127.0.0.1:7862**, **click once** to enable audio (browsers block
autoplay until a user gesture), then press **Start the station**.

Override the model with `MBS_MODEL`, e.g. `MBS_MODEL=qwen3:4b`.

### 4. Compare models

```bash
python3 scripts/ab_model.py qwen3:8b qwen3:30b-a3b
```

Scores any two models on latency, turn count, repetition, and fact coverage so
a model swap is an objective decision, not a guess.

---

## How a broadcast is made

```
live RSS ─▶ ingest ─▶ compile ─▶ cluster ─▶ rank ─▶ compose ─▶ validate ─▶ speak
 (25 feeds)  (§7)     (§14)     (stories)   (§10)   (§12.4)     (§15.4)    (TTS)
```

| Stage | What happens |
|---|---|
| **Ingest** | RSS fetched through an SSRF/size policy, canonically normalized, deduplicated at five levels, full-text enriched where the feed carries it. |
| **Compile** | 8 deterministic passes: structure, entities, claims, evidence, contradiction, integration. Every claim keeps provenance to a source passage. |
| **Cluster** | Claims sharing a source article or identity group into stories. Facts from one article stay together. |
| **Rank** | 9 factors — importance, novelty, velocity, evidence, reliability, uncertainty, audience, coverage debt, saturation. |
| **Compose** | A story with 2+ facts goes to three independent personas; a thin story to a single-voice editor. |
| **Ground** | Every composed turn is checked against the source facts. Invented entities or figures are dropped. |
| **Validate** | 8-stage script validation: schema, evidence, contradiction, attribution, speculation, currency, prohibited disclosure, delivery. |
| **Speak** | Synthesized per speaker-group in one cloned voice, buffered, played out, recorded to the transcript. |

---

## Architecture

The system is layered so that a change in one layer does not leak into another.

| Layer | Responsibility |
|---|---|
| `core/models.py` | The domain model — every entity and field (spec §5). |
| `core/persistence.py` | SQLite store: 15 tables, FK constraints, WAL, append-only triggers. |
| `core/ingestion/` | Policy-enforcing fetch, canonicalization, dedup, scheduling, article-body enrichment. |
| `core/compiler/` | 8-pass knowledge compilation: CKU identity, claims, evidence, contradiction, integration. |
| `core/world_model/` | Change detection, 9-factor ranking, coverage debt/saturation, story clustering, temporal replay. |
| `core/broadcast/` | Script validation, segment lifecycle, audio buffering, corrections. |
| `core/personas/` | Independent analysis, disagreement detection and guards, memory, consistency, dialogue. |
| `core/audience/` | Message pipeline, rate limiting, abuse classification, feedback. |
| `core/api/` | 20 endpoints with the API's own response types, SSE events, operator console. |
| `core/reliability/` | Health, routing, caching, scheduling, streaming, recovery, metrics. |
| `core/inference/` | The live runtime: providers, autonomous loop, editor, voices, world sources, browser console. |

Full detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## The epistemic guarantees

The point of the system is not that it talks. It is that it can be trusted about
*why* it says what it says. These are enforced in code, not in prompts:

- **Evidence ≠ opinion.** A claim carries provenance to a source passage.
  Personas interpret significance; they never re-decide whether a fact is true.
- **The model proposes; deterministic code decides.** Every composed turn is
  checked against the source facts. Invented entities and figures are dropped;
  if nothing survives, the segment is cancelled rather than aired.
- **No manufactured debate.** When the evidence supports a straightforward
  conclusion, disagreement is suppressed instead of staged. Unresolved questions
  stay unresolved.
- **Never fabricate a timestamp.** A missing observation time stays null.
- **Fail closed.** If a check cannot be performed, the conservative outcome is
  the default — including refusing a non-local inference endpoint.

The full list of invariants: [`docs/EPISTEMIC_INVARIANTS.md`](docs/EPISTEMIC_INVARIANTS.md).
Decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md) and
[`docs/DECISIONS_MULTIAGENT.md`](docs/DECISIONS_MULTIAGENT.md).

---

## Documentation

Start with [`docs/README.md`](docs/README.md) for the reading order.

| Doc | Read it for |
|---|---|
| [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md) | Objectives, non-goals, operating modes, the control loop. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, module boundaries, provider contracts, security posture. |
| [docs/DOMAIN_MODEL.md](docs/DOMAIN_MODEL.md) | The canonical data model — every entity and field. |
| [docs/EPISTEMIC_INVARIANTS.md](docs/EPISTEMIC_INVARIANTS.md) | The distinctions the system MUST preserve. |
| [docs/EDITORIAL_MODEL.md](docs/EDITORIAL_MODEL.md) | How the broadcast decides what to say. |
| [docs/DECISIONS_MULTIAGENT.md](docs/DECISIONS_MULTIAGENT.md) | Grounding floor, dialogue policy, voice consistency (ADR-057–060). |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phases 0–9 with exit criteria. |
| [docs/ACCEPTANCE_CRITERIA.md](docs/ACCEPTANCE_CRITERIA.md) | Gates 1–10 and the Definition of Done. |
| [docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md) | How the system is verified. |

**Specification:** the authoritative source is the published spec (v1.0,
2026-10-12, 31 sections):
<https://www.danielkliewer.com/blog/2026-10-12-mindbroadcast>
Docs cite it as `§N`. Where a doc and the spec disagree, the spec wins.

---

## Roadmap

```
Phase 0  Audit                      ✅ COMPLETE (49 pass / 27 fail baseline reproduced)
Phase 1  Persistence + domain       ✅ COMPLETE — Gate 1 MET
Phase 2  Ingestion + dedup          ✅ COMPLETE — Gate 2 MET
Phase 3  Knowledge compilation      ✅ COMPLETE — Gate 3 MET
Phase 4  World model + stories      ✅ COMPLETE — Gates 4, 5 MET
Phase 5  Single-persona broadcast   ✅ COMPLETE — Gate 6 MET
Phase 6  Personas + debate          ✅ COMPLETE
Phase 7  Audience + operator UI     ✅ COMPLETE — Gate 9 MET
Phase 8  Reliability + streaming    ✅ COMPLETE — Gates 7, 10 MET
Phase 9  Recursive improvement      ⬜
```

Beyond the phases, the live runtime was built and verified end-to-end against
real models: inference providers, a persistent TTS server, the autonomous loop,
the browser console, world-wide sources, multi-agent composition, and a
consistent cloned voice.

---

## Tests

**959 passing, 0 failing.**

```bash
python3 -m pytest -q
```

| Suite | Tests | Covers |
|---|---:|---|
| `test_broadcast.py` | 151 | Script validation, lifecycle, audio, corrections |
| `test_ingestion.py` | 104 | Fetch policy, canonicalization, dedup, scheduling |
| `test_reliability.py` | 101 | Health, routing, cache, streaming, recovery |
| `test_persistence.py` | 90 | Schema, append-only triggers, snapshots |
| `test_audience_api.py` | 90 | Message pipeline, 20 endpoints, SSE |
| `test_personas.py` | 81 | Memory, disagreement, consistency, dialogue |
| `test_compiler.py` | 71 | 8 passes, CKU identity, integration |
| `test_models.py` | 55 | Domain model, enums, status mapping |
| `test_world_model.py` | 54 | Ranking, coverage, clustering, replay |
| `test_migrations.py` | 34 | Versioned up/down, ledger |
| `test_epistemic.py` | 32 | The invariants |
| `test_recovery.py` | 24 | Startup recovery, mode transitions |
| `test_snapshot.py` | 18 | Snapshot service |
| `test_editor.py` | 15 | Grounding floor, editorial composition |
| `test_voices.py` | 14 | Voice consistency, delivery variety |
| `test_llm_analysis.py` | 14 | Persona analysis, §12.3 floor |
| `test_world_sources.py` | 11 | Registry coverage, idempotent registration |

---

## Engineering rules

- **The full test suite must pass.** A phase with known failures is not complete.
- **A feature is done when its gate passes on a real run**, not when a happy
  path was demonstrated once.
- **Fail closed.** When a check cannot be performed, the conservative outcome
  (do not broadcast / do not commit) is the default.
- **Root cause, not band-aids.** A partial-suppression fix is not a fix.
- **Real verification.** Report actual test output; never fabricate a result.
- **ADR before code.** Ratify the decision, then implement.
- **Local commits by default.** Nothing is pushed without explicit instruction.

---

## Scripts

| Script | Purpose |
|---|---|
| `scripts/live_e2e.py` | End-to-end proof against a real model: ingest → compile → rank → compose → validate → synthesize. |
| `scripts/ab_model.py` | A/B two models on the real longform prompt; scores repetition and coverage. |
| `scripts/probe_feeds.py` | Verify candidate RSS feeds are live before curating them into the registry. |

---

## Prior implementation

An earlier implementation exists at
[`github.com/kliewerdaniel/MindBroadcast`](https://github.com/kliewerdaniel/MindBroadcast)
(~1,078 lines: SQLite persistence, 7 domain dataclasses). Phase 0 audited it and
reproduced its baseline; Phase 1 resolved its defects.

This is **not** a code-level continuation of that repo. The schema is new
(15 tables, `SCHEMA_VERSION=2`); legacy data arrives through
`core/legacy_import.py`, which is tested to preserve every timestamp and keep
corrections out of the evidence table. See ADR-002 in
[`docs/DECISIONS.md`](docs/DECISIONS.md).

---

## License

MIT
