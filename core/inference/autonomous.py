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
    ):
        self.runtime = runtime
        self.store = store or WorldStore(db_path)
        self.ranker = ranker or StoryRanker()
        self.fetcher = Fetcher(transport=AiohttpTransport())
        self.ingestion = IngestionPipeline(self.store, self.fetcher)
        self.compiler = KnowledgeCompiler(self.store)
        self.story_engine = StoryEngine(self.store)
        self.cycles: List[BroadcastCycle] = []

    async def register_source(self, name: str, endpoint: str) -> str:
        source = Source(name=name, endpoint=endpoint,
                        source_type=SourceType.RSS)
        return await self.store.add_source(source)

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

        # 2. Compile newly captured articles into claims/evidence.
        new_articles = await self._recent_articles(source.id,
                                                   cycle.articles_ingested)
        claims_compiled = 0
        for article in new_articles:
            result = await self.compiler.compile(article)
            claims_compiled += len(getattr(result.claims, "claims", []))
        cycle.claims_compiled = claims_compiled

        # 3. Cluster claims into stories.
        candidates = self.story_engine.cluster(
            await self._claim_records())
        cycle.stories_ranked = len(candidates)

        if not candidates:
            cycle.silent_reason = "no stories assembled from ingested claims"
            self.cycles.append(cycle)
            return cycle

        # 4. Rank stories and pick the top one (§10).
        facts = [self._story_facts(c) for c in candidates]
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
        self.cycles.append(cycle)
        return cycle

    # ── helpers ───────────────────────────────────────────────────────

    async def _pick_source(self, source_id: Optional[str]) -> Optional[Source]:
        if source_id:
            return await self.store.get_source(source_id)
        sources = await self.store.list_sources(enabled_only=True)
        return sources[0] if sources else None

    async def _recent_articles(self, source_id: str, limit: int
                               ) -> List[Any]:
        articles = await self.store.list_articles()
        from_source = [a for a in articles if a.source_id == source_id]
        return from_source[-limit:] if limit else from_source

    async def _claim_records(self) -> List[Any]:
        from ..world_model.stories import ClaimRecord
        claims = await self.store.list_claims()
        records = []
        for claim in claims:
            records.append(ClaimRecord(
                claim_id=claim.id,
                proposition=claim.canonical_proposition,
                claim_type=claim.claim_type.value,
                epistemic_status=claim.epistemic_status.value,
                first_observed_at=claim.first_observed_at,
            ))
        return records

    def _story_facts(self, candidate: Any) -> StoryFacts:
        return StoryFacts(story_id=candidate.story_id,
                          title=candidate.canonical_title,
                          now=datetime.now(timezone.utc))

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
        for claim_id in candidate.claim_ids:
            claim = await self.store.get_claim(claim_id)
            if claim is None:
                continue
            claims.append(claim_id)
            facts.append(claim.canonical_proposition)
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
