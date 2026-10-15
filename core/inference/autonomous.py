"""The autonomous broadcaster: watches sources, decides, and speaks.

This closes the input half of the pipeline that the console demo bypassed.
Instead of a human typing facts into a form, the broadcaster:

    ingest a real source (live HTTP) -> compile new articles into claims
    -> cluster claims into stories -> rank stories -> pick the top story
    -> build an evidence packet from that story's real claims
    -> hand the packet to the LiveRuntime, which generates, validates,
       synthesizes, and airs it.

The decision — what to broadcast — now comes from the world model, not from a
hardcoded topic. The runtime's job is unchanged: it turns an evidence packet
into speech. This module's job is to produce the packet from what the system
actually observed.

Fail-closed and honest throughout: if a source is unreachable, or no story is
worth airing, the broadcaster reports that and stays silent rather than
inventing a bulletin (§27.4).
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..compiler.orchestrator import KnowledgeCompiler
from ..ingestion.pipeline import IngestionPipeline
from ..ingestion.fetcher import Fetcher
from ..ingestion.transport import AiohttpTransport
from ..models import Source, SourceType, Story
from ..persistence import WorldStore
from ..world_model.ranking import StoryFacts, StoryRanker
from ..world_model.stories import StoryEngine
from .runtime import LiveRuntime, RuntimeConfig, TranscriptLine


# Bound the compile backlog per cycle (§23.3: work is bounded). A cycle that
# inherits a large never-compiled backlog drains it over several cycles rather
# than blocking on a single unbounded compile pass.
_MAX_COMPILE_PER_CYCLE = 15

# Facts per aired segment. Enough for a real multi-turn bulletin / multi-persona
# debate, bounded so a long feature article does not become an exhaustive read.
_MAX_FACTS_PER_SEGMENT = 6


@dataclass
class BroadcastCycle:
    """What one ingest->decide->broadcast cycle observed and did."""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    articles_ingested: int = 0
    claims_compiled: int = 0
    stories_ranked: int = 0
    chosen_story_id: Optional[str] = None
    chosen_story_title: str = ""
    segment: Optional[TranscriptLine] = None
    silent_reason: str = ""

    @property
    def aired(self) -> bool:
        return self.segment is not None


class AutonomousBroadcaster:
    """Watches sources and broadcasts what the world model decides is worth
    saying — the real MindBroadcast loop."""

    def __init__(
        self,
        runtime: LiveRuntime,
        store: Optional[WorldStore] = None,
        ranker: Optional[StoryRanker] = None,
        db_path: str = ":memory:",
        on_segment: Optional[Any] = None,
    ):
        self.runtime = runtime
        self.on_segment = on_segment
        self.store = store or WorldStore(db_path)
        self.ranker = ranker or StoryRanker()
        self.fetcher = Fetcher(transport=AiohttpTransport())
        self.ingestion = IngestionPipeline(self.store, self.fetcher)
        self.compiler = KnowledgeCompiler(self.store)
        self.story_engine = StoryEngine(self.store)
        self.cycles: List[BroadcastCycle] = []
        # Durable continuity state (§30 "survives process restarts"): the set
        # of aired story ids is persisted to a sibling file so a restart does
        # not re-air. Operational state, not domain data — so a small JSON
        # file, not a schema migration.
        self._state_path = self._derive_state_path(db_path)
        # Continuity: claim ids already aired, so a continuous loop never
        # re-broadcasts the same story. Without this, re-clustering all claims
        # each cycle would re-air the top story forever.
        self._aired_claim_ids: set = set()
        self._aired_story_ids: set = set()
        self._source_cursor: int = 0
        self._stop = False
        self._load_state()

    def _derive_state_path(self, db_path: str) -> Optional[str]:
        """A sibling JSON file for continuity state; None for in-memory DBs."""
        if db_path in (":memory:", ""):
            return None
        return db_path + ".aired.json"

    def _load_state(self) -> None:
        """Restore aired story ids so a restart does not re-air (§30)."""
        if self._state_path is None:
            return
        try:
            with open(self._state_path) as handle:
                data = json.load(handle)
            self._aired_story_ids = set(data.get("aired_story_ids", []))
            self._aired_claim_ids = set(data.get("aired_claim_ids", []))
        except (FileNotFoundError, ValueError, OSError):
            pass    # no prior state — start fresh

    def _save_state(self) -> None:
        if self._state_path is None:
            return
        try:
            with open(self._state_path, "w") as handle:
                json.dump({
                    "aired_story_ids": sorted(self._aired_story_ids),
                    "aired_claim_ids": sorted(self._aired_claim_ids),
                }, handle)
        except OSError:
            pass    # persistence is best-effort; continuity degrades to
            # in-memory rather than crashing the broadcast

    async def register_source(self, name: str, endpoint: str) -> str:
        # Idempotent on endpoint: re-registering a feed returns the existing
        # source rather than creating a duplicate. The endpoint is the stable
        # identity; the display name is cosmetic.
        existing = await self.store.find_source_by_endpoint(endpoint)
        if existing is not None:
            return existing.id
        source = Source(name=name, endpoint=endpoint,
                        source_type=SourceType.RSS)
        return await self.store.add_source(source)

    async def register_world_sources(
        self, regions: Optional[List[str]] = None
    ) -> Dict[str, str]:
        """Register the curated world feeds; return {name: source_id}.

        Region-balanced so the broadcast draws from across the world, not one
        or two Anglophone outlets. Idempotent — safe to call repeatedly; only
        new endpoints create rows. `regions` limits to a subset.
        """
        from .world_sources import WORLD_FEEDS, all_feeds
        if regions:
            feeds = [s for r in regions for s in WORLD_FEEDS.get(r, [])]
        else:
            feeds = all_feeds()
        registered: Dict[str, str] = {}
        for feed in feeds:
            registered[feed.name] = await self.register_source(
                feed.name, feed.url)
        return registered

    async def run_cycle(self, source_id: Optional[str] = None
                        ) -> BroadcastCycle:
        """One full cycle: ingest -> compile -> cluster -> rank -> broadcast."""
        cycle = BroadcastCycle()
        source = await self._pick_source(source_id)
        if source is None:
            cycle.silent_reason = "no source available"
            self.cycles.append(cycle)
            return cycle

        # 1. Ingest (live HTTP, through the SSRF/size policy).
        report = await self.ingestion.ingest_source(source)
        counts = report.counts()
        cycle.articles_ingested = counts.get("captured", 0) + counts.get(
            "revision", 0)
        if report.error:
            cycle.silent_reason = f"ingestion failed: {report.error}"
            self.cycles.append(cycle)
            return cycle

        # 2. Compile articles that have not been compiled yet into
        # claims/evidence. Selecting by "last N ingested" left a backlog of
        # never-compiled full-text articles untouched (each yields many
        # claims), which is why broadcasts were one-fact and short. Compile the
        # backlog, bounded per cycle so one cycle cannot run unbounded.
        new_articles = await self._uncompiled_articles(source.id)
        claims_compiled = 0
        for article in new_articles[:_MAX_COMPILE_PER_CYCLE]:
            result = await self.compiler.compile(article)
            claims_compiled += len(getattr(result.claims, "claims", []))
        cycle.claims_compiled = claims_compiled

        # 3. Cluster claims into stories.
        candidates = self.story_engine.cluster(
            await self._claim_records())
        cycle.stories_ranked = len(candidates)

        # 3a. Drop stories whose claims are feed boilerplate, not news. An RSS
        # channel's own description ("Top stories, breaking news... follow news
        # topics") compiles into a claim like any other; airing it is noise.
        candidates = [c for c in candidates
                      if not self._is_boilerplate(c)]
        # 3b. Continuity: drop stories already aired, so a continuous loop
        # advances to fresh stories instead of repeating itself.
        candidates = [c for c in candidates if not self._already_aired(c)]
        if not candidates:
            cycle.silent_reason = ("no fresh stories to broadcast "
                                   "(everything ingested has already aired)")
            self.cycles.append(cycle)
            return cycle

        # 4. Rank stories with real signal from their claims (§10) and pick
        # the top one. Empty facts make every story score equally, so the
        # ranker would pick arbitrarily — the signal below is what makes the
        # choice meaningful.
        facts = [await self._story_facts(c) for c in candidates]
        from ..world_model.ranking import rank_stories
        ranked = rank_stories(self.ranker, facts)
        if not ranked:
            cycle.silent_reason = "no eligible story to broadcast"
            self.cycles.append(cycle)
            return cycle
        top = ranked[0]
        chosen = self._candidate_for(top.story_id, candidates)
        cycle.chosen_story_id = top.story_id
        cycle.chosen_story_title = chosen.canonical_title if chosen else ""

        # 5. Build an evidence packet from the story's REAL claims.
        topic = await self._topic_from_story(chosen)
        if topic is None:
            cycle.silent_reason = "chosen story has no usable claims"
            self.cycles.append(cycle)
            return cycle

        # 6. Broadcast it through the live runtime.
        line = await self.runtime._produce_segment(topic)
        cycle.segment = line
        if line is None:
            cycle.silent_reason = "; ".join(self.runtime.errors[-1:]) or \
                "generation produced no segment"
        else:
            # Continuity: remember what aired so the loop advances.
            self._mark_aired(chosen)
        if line is not None and self.on_segment is not None:
            # Notify the listener (console/SSE) exactly as runtime.run() would,
            # so the autonomous path and the demo path surface segments alike.
            self.on_segment(line)
        self.cycles.append(cycle)
        return cycle

    async def run_forever(self, source_id: Optional[str] = None,
                          poll_seconds: float = 120.0,
                          on_cycle: Optional[Any] = None,
                          max_cycles: int = 0) -> None:
        """The continuous station: watch, decide, air, repeat.

        Sleeps between cycles and stays silent when the feed has nothing fresh
        (§27.4) rather than re-airing old stories. `max_cycles` bounds it for
        testing; 0 runs until `stop()`.
        """
        import asyncio
        self._stop = False
        count = 0
        while not self._stop:
            cycle = await self.run_cycle(source_id)
            count += 1
            if on_cycle is not None:
                on_cycle(cycle)
            if max_cycles and count >= max_cycles:
                break
            if self._stop:
                break
            await asyncio.sleep(poll_seconds)

    def stop(self) -> None:
        self._stop = True

    def _already_aired(self, candidate: Any) -> bool:
        # A story is identified by its story_id (stable via _identity_key), not
        # by its claim set — clustering can merge additional claims into an
        # existing story between cycles, so a growing claim_ids list is not a
        # new story. Keying on story_id is what makes the loop advance.
        return candidate.story_id in self._aired_story_ids

    def _mark_aired(self, candidate: Any) -> None:
        self._aired_story_ids.add(candidate.story_id)
        self._aired_claim_ids.update(getattr(candidate, "claim_ids", []))
        self._save_state()

    # ── helpers ───────────────────────────────────────────────────────

    async def _pick_source(self, source_id: Optional[str]) -> Optional[Source]:
        if source_id:
            return await self.store.get_source(source_id)
        # Not pinned: rotate through enabled sources so a multi-source
        # station actually ingests from all of them across cycles, rather
        # than starving every source after the first (§3 multi-source).
        sources = await self.store.list_sources(enabled_only=True)
        if not sources:
            return None
        chosen = sources[self._source_cursor % len(sources)]
        self._source_cursor += 1
        return chosen

    async def _recent_articles(self, source_id: str, limit: int
                               ) -> List[Any]:
        articles = await self.store.list_articles()
        from_source = [a for a in articles if a.source_id == source_id]
        return from_source[-limit:] if limit else from_source

    async def _uncompiled_articles(self, source_id: Optional[str] = None
                                   ) -> List[Any]:
        """Articles that have produced no claims yet — the compile backlog.

        Selecting by "last N ingested" is wrong: a cycle that ingests few
        articles re-compiles old ones while a backlog of never-compiled
        full-text articles (which yield many claims each) sits untouched. That
        is why stories came out one-fact and short. Compile whatever has no
        claims, which also self-heals a backlog from an earlier bug.
        """
        articles = await self.store.list_articles()
        if source_id:
            articles = [a for a in articles if a.source_id == source_id]
        have_claims = await self.store.claim_article_ids()
        compiled = set(have_claims.values())
        return [a for a in articles if a.id not in compiled]

    async def _claim_records(self) -> List[Any]:
        from ..world_model.stories import ClaimRecord
        claims = await self.store.list_claims()
        # Claims carry no article column; derive it via the evidence join so
        # facts from one article cluster into one story.
        article_of = await self.store.claim_article_ids()
        records = []
        for claim in claims:
            records.append(ClaimRecord(
                claim_id=claim.id,
                proposition=claim.canonical_proposition,
                claim_type=claim.claim_type.value,
                epistemic_status=claim.epistemic_status.value,
                first_observed_at=claim.first_observed_at,
                source_article_id=article_of.get(claim.id),
            ))
        return records

    async def _story_facts(self, candidate: Any) -> StoryFacts:
        """Build ranking signal from the story's real claims.

        Populates the fields the ranker actually reads — freshness
        (material_changes), primary-evidence count, and independent-source
        groups — so the choice of what to air reflects the content rather than
        being arbitrary.
        """
        from ..world_model.changes import MaterialChange, ChangeKind
        facts = StoryFacts(story_id=candidate.story_id,
                           title=candidate.canonical_title,
                           now=datetime.now(timezone.utc))
        claim_texts: List[str] = []
        newest: Optional[datetime] = None
        for claim_id in getattr(candidate, "claim_ids", []):
            claim = await self.store.get_claim(claim_id)
            if claim is None:
                continue
            claim_texts.append(claim.canonical_proposition)
            if newest is None or claim.first_observed_at > newest:
                newest = claim.first_observed_at
            # A quantitative or observational claim is primary evidence.
            if claim.claim_type.value in ("quantitative", "observational"):
                facts.primary_evidence_count += 1
            else:
                facts.attributed_evidence_count += 1
        if newest is not None:
            facts.last_material_update_at = newest
            facts.material_changes = [MaterialChange(
                kind=ChangeKind.NEW_EVENT, significance=1.0, occurred_at=newest)]
        # Each distinct claim is treated as its own source group here; a real
        # ownership graph would collapse same-owner outlets, but distinct
        # headlines are distinct observations.
        facts.independent_source_groups = len(claim_texts)
        facts.total_source_groups = max(len(claim_texts), 1)
        return facts

    @staticmethod
    def _is_boilerplate(candidate: Any) -> bool:
        """True when a story's claims are feed boilerplate, not news.

        RSS channels emit a description like "Top stories, breaking news, live
        reporting... follow news topics" that compiles into a claim. These
        phrases are the tell.
        """
        markers = (
            "top stories", "breaking news", "live reporting",
            "follow news topics", "news topics that match",
            "sign up", "subscribe", "download the",
        )
        text = (candidate.canonical_title or "").lower()
        return any(m in text for m in markers)

    def _candidate_for(self, story_id: str, candidates: List[Any]
                       ) -> Optional[Any]:
        for candidate in candidates:
            if candidate.story_id == story_id:
                return candidate
        return candidates[0] if candidates else None

    async def _topic_from_story(self, candidate: Any) -> Optional[Dict[str, Any]]:
        """Turn a story candidate into a runtime topic grounded in real claims."""
        if candidate is None or not candidate.claim_ids:
            return None
        claims = []
        facts = []
        seen: set = set()
        for claim_id in candidate.claim_ids:
            claim = await self.store.get_claim(claim_id)
            if claim is None:
                continue
            # Deduplicate near-identical propositions before they reach the
            # prompt. The compiler can extract the same sentence more than
            # once, and a strict one-turn-per-fact prompt would then rewrite
            # the duplicate — producing the same sentence spoken two or three
            # times. Key on normalized text so only genuinely distinct facts
            # become turns.
            key = _normalize_proposition(claim.canonical_proposition)
            if key in seen:
                continue
            seen.add(key)
            claims.append(claim_id)
            facts.append(claim.canonical_proposition)
            # Cap facts per segment (§23.3 bounded work). A long feature article
            # can yield dozens of distinct claims; airing all of them would be
            # an exhaustive read, not a broadcast. Keep the lead facts; the
            # rest remain in the world model for a later segment.
            if len(facts) >= _MAX_FACTS_PER_SEGMENT:
                break
        if not facts:
            return None
        return {
            "objective": (f"Report the developing story: "
                          f"{candidate.canonical_title}."),
            "facts": facts,
            "claims": [_Rec(c) for c in claims],
            "evidence": [_Rec(c) for c in claims],
        }


class _Rec:
    """A minimal record with an id, for the context builder."""
    def __init__(self, id: str):
        self.id = id


def _normalize_proposition(text: str) -> str:
    """Collapse a proposition to a dedup key: lowercase alphanumeric words.

    Two claims about the same fact in different surface wording (e.g. a
    trailing article credit, or the same clause re-emitted by the compiler)
    normalize to the same key and are treated as one fact. Deliberately
    coarse: for broadcast we would rather drop a borderline duplicate than
    speak the same sentence twice.
    """
    import re
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))
