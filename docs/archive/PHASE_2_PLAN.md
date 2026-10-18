# Phase 2 Plan — Ingestion and Source Normalization

**Objective:** Build a durable pipeline from external sources to normalized
articles. (Spec §25 Phase 2, §7)
**Gate 2:** Ingestion and deduplication are idempotent.

**Entry state:** Phase 1 complete, Gate 1 MET — 253 tests passing.

---

## Scope

Spec §7 decomposes into six concerns. This phase builds all six, with the
security requirements (§7.6) treated as load-bearing rather than as hardening
bolted on later.

| Concern | Spec | Module |
|---|---|---|
| Source adapters | §7.2 | `core/ingestion/adapters.py` |
| Scheduling + backpressure | §7.3 | `core/ingestion/scheduler.py` |
| Canonicalization | §7.4 | `core/ingestion/canonicalization.py` |
| Deduplication | §7.5 | `core/ingestion/deduplication.py` |
| Content security | §7.6 | `core/ingestion/security.py` |
| Pipeline orchestration | §7.1 | `core/ingestion/pipeline.py` |

## Design decisions to make before code

### D1. Adapter interface

Spec §7.2's illustrative contract, adapted to this codebase's async style:

```python
class SourceAdapter(ABC):
    async def fetch_updates(self, cursor=None) -> FetchResult
    async def parse_response(self, response) -> List[NormalizedEntry]
    async def health_check(self) -> HealthStatus
    async def get_checkpoint(self) -> Optional[str]
    async def restore_checkpoint(self, checkpoint: str) -> None
```

`NormalizedEntry` is the normalized output: title, link, published_at (nullable
— **never invented**), summary, content, author, guid. Every adapter must report
errors explicitly and preserve missing timestamps as `None` (§7.2).

### D2. Checkpoint storage

Spec §7.2 requires checkpoint handling. Store the cursor on the Source row as
operational state — a new column, not a new table. A checkpoint is mutable
operational state whose change is reconstructable from the event log, which
§3.3 explicitly permits.

### D3. Deduplication levels must stay separate

Spec §7.5 is emphatic that the five levels must not be conflated, and that a
duplicate article must not create a new event merely because its URL differs.
The implementation therefore returns a **classified** result rather than a
boolean:

```python
class DuplicateKind(Enum):
    EXACT_URL          # same canonical URL
    EXACT_CONTENT      # different URL, identical content hash
    NEAR_DUPLICATE     # high text similarity
    SYNDICATED         # same content, different source
    NONE
```

`NEAR_DUPLICATE` and `SYNDICATED` produce **candidate relationships with
confidence**, never irreversible merges (§7.5).

### D4. Canonicalization is conservative and source-aware

Spec §7.4: query parameters cannot universally be discarded. The policy is
configurable, and every transformation is recorded so unexpected merges can be
investigated. The function returns the canonical URL **plus the list of rules
applied**, not just a string.

### D5. Security is a gate, not a filter

Spec §7.6. The fetch layer validates scheme, blocks loopback/private/link-local
and cloud-metadata addresses, bounds size and timeouts, and restricts redirects
— with redirect targets re-checked against the same policy. This is enforced
before any parsing, so no adapter can accidentally bypass it.

---

## Work streams

### A — Security and fetching (`security.py`, `fetcher.py`)

- URL scheme allowlist (http/https only).
- SSRF guard: reject loopback, private, link-local, reserved, and
  `169.254.169.254` (cloud metadata). **Re-check after every redirect.**
- Response size cap, connect/read timeouts, redirect limit.
- Structured `FetchError` with a category so the scheduler can distinguish
  transient from permanent failures.

**Tests:** each blocked target raises; a redirect to a blocked host is rejected;
oversized response is rejected; timeout is bounded. Uses a local stub server —
no real network.

### B — Canonicalization (`canonicalization.py`)

- Host lowercasing, known tracking-parameter removal, safe path normalization.
- **Configurable**: a per-source policy may preserve query parameters.
- Returns `CanonicalizationResult(canonical_url, rules_applied, final_url,
  redirect_chain)`.

**Tests:** tracking params stripped; meaningful query params preserved when
policy says so; rules recorded; `first_seen_at` vs `published_at` unaffected.

### C — Adapters (`adapters.py`)

- `RSSAdapter` and `AtomAdapter` over `feedparser` (it handles both).
- Common base implementing the §7.2 contract.
- Malformed feed → `AdapterError`, never a crash (§7.7 criterion 2).
- Missing publication timestamp → `None`, preserved (§7.2).

**Tests:** parse a fixture feed; malformed feed raises `AdapterError`;
missing `published` yields `None`; checkpoint round-trips.

### D — Deduplication (`deduplication.py`)

- Canonical URL match → existing article.
- Content hash match → `EXACT_CONTENT` / `SYNDICATED`.
- Near-duplicate: shingle-based similarity over normalized text, threshold
  configurable, **conservative default** (prefer associating over discarding,
  because a near-duplicate may carry a correction — invariant I9).
- Returns classified candidates with confidence.

**Tests:** each of the five levels detected and not conflated; a near-duplicate
carrying a changed figure is **retained**, not discarded; similarity is
symmetric; threshold behaviour is deterministic.

### E — Scheduler (`scheduler.py`)

- Per-source poll interval from `Source.poll_interval_seconds`.
- Exponential backoff with jitter on repeated transient failure.
- Bounded concurrency; one failing source cannot starve the others (§7.3).
- Backpressure: **preserve incoming observations over expensive analysis**
  (§7.3) — capture is priority work.
- Due-source computation is a pure function of `now`, source config, last
  attempt/success, and failure count.

**Tests:** backoff grows and is jittered; a source with failures is polled less
often; due-source selection is deterministic given a clock; concurrency is
bounded.

### F — Pipeline (`pipeline.py`)

The §7.1 flow, with each step's durability guarantee:

```
fetch → validate → parse → canonicalize → dedupe → durable capture → outbox
```

- **A new article is durably stored before downstream processing is scheduled**
  (§7.7 criterion 4) — the outbox row commits in the same transaction as the
  article, using `enqueue_outbox_in()` from ADR-013.
- Idempotent on retry (§7.7 criterion 7) via the idempotency key.
- A corrected article becomes a new version via `supersede_article`
  (§7.7 criterion 9).

**Tests:** end-to-end against a stub adapter; malformed feed does not crash;
duplicate fetch creates no second article; article + outbox commit atomically;
a restarted process recovers pending ingestion work.

---

## Exit criteria — Gate 2

All ten §7.7 acceptance criteria, each with a real test:

- [ ] 1. A configured feed can be fetched and parsed.
- [ ] 2. A malformed feed does not crash the application.
- [ ] 3. Duplicate fetches do not create duplicate active articles.
- [ ] 4. A new article is durably stored before downstream processing is scheduled.
- [ ] 5. Source and observation timestamps are preserved separately.
- [ ] 6. Network failure does not corrupt previously captured information.
- [ ] 7. Retried work is idempotent.
- [ ] 8. A restarted process can recover pending ingestion tasks.
- [ ] 9. A corrected or revised article remains distinguishable from its earlier captured version.
- [ ] 10. External content cannot directly execute application commands.

Plus: full suite green, and every Gate 1 test still passing.

## Explicitly out of scope

Knowledge compilation (entity/claim/evidence extraction), editorial planning,
LLM calls. Gate 2 is about **capture** — spec §7.1: ingestion "should not
determine the final editorial meaning of every article."
