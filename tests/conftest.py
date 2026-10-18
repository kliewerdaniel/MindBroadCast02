"""Shared test fixtures.

The `world_store` fixture is deliberately synchronous: WorldStore's
constructor is not async, and a fixture declared `async def` hands the test the
async-generator object rather than the store. That bug caused 19 of the 27
failures in the pre-Phase-1 suite.

Each test gets a private in-memory database — no module-level sharing, so
stores cannot see each other's data.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from core.models import (
    Article,
    Claim,
    ClaimStatus,
    ClaimType,
    Correction,
    CorrectionType,
    Entity,
    EntityType,
    Event,
    EventStatus,
    Evidence,
    Source,
    SourceType,
    Story,
    StoryStatus,
)
from core.persistence import WorldStore


def run(coro):
    """Run a coroutine to completion on a private event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def world_store():
    """A fresh WorldStore backed by a private in-memory database."""
    store = WorldStore(db_path=":memory:")
    yield store
    store.close()


@pytest.fixture
def db_path(tmp_path):
    """An on-disk database path, for cross-process persistence tests."""
    return str(tmp_path / "test.db")


@pytest.fixture
def sample_source():
    return Source(
        name="Reuters",
        canonical_domain="reuters.com",
        source_type=SourceType.RSS,
        endpoint="https://www.reuters.com/arc/outboundfeeds/rss/",
        publisher_identity="Thomson Reuters",
    )


@pytest.fixture
def sample_article(sample_source):
    return Article(
        source_id=sample_source.id,
        canonical_url="https://www.reuters.com/world/article-1",
        title="Climate Summit Begins",
        content_hash="a" * 64,
        normalized_text="World leaders gathered for the climate summit.",
        published_at=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
    )


@pytest.fixture
def sample_story():
    return Story(
        canonical_title="Test Story: Climate Summit",
        editorial_summary="World leaders meet to discuss climate policy.",
        status=StoryStatus.ACTIVE,
        entity_ids=["entity-1", "entity-2"],
        claim_ids=["claim-1"],
        confidence=0.7,
    )


@pytest.fixture
def sample_event():
    return Event(
        canonical_title="Climate Summit Begins",
        event_type="summit",
        summary="World leaders gathered for the climate summit.",
        status=EventStatus.DEVELOPING,
        occurred_at_start=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
    )


@pytest.fixture
def sample_claim():
    return Claim(
        canonical_proposition="Global temperatures rose 1.5C in 2025.",
        claim_type=ClaimType.QUANTITATIVE,
        epistemic_status=ClaimStatus.UNASSESSED,
        confidence=0.6,
    )


@pytest.fixture
def sample_evidence(sample_article, sample_source):
    return Evidence(
        evidence_type="passage",
        article_id=sample_article.id,
        source_id=sample_source.id,
        content_reference="paragraph-3",
        excerpt="Temperature data from NOAA shows a 1.5C increase.",
        content_hash="b" * 64,
        extraction_method="llm",
        extraction_model="qwen3:8b",
        extraction_version="v1",
    )


@pytest.fixture
def sample_entity():
    return Entity(
        canonical_name="Climate Summit 2025",
        entity_type=EntityType.CONCEPT,
        aliases=["COP30", "Climate Conference"],
        external_identifiers={"wikidata": "Q123"},
    )


@pytest.fixture
def sample_correction():
    return Correction(
        correction_type=CorrectionType.PUBLISHER,
        target_type="claim",
        target_id="claim-1",
        reason="Publisher restated the figure as 1.4C.",
        original_value_reference="claim-v1",
        corrected_value_reference="claim-v2",
    )
