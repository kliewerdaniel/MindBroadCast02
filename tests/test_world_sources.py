"""Tests for the world source registry and idempotent registration.

Coverage is the requirement: the registry spans every region, every URL was
probed live, and registration is idempotent so re-registering never creates
duplicate sources.
"""

import pytest
pytest_plugins = []


def pytest_configure(config):
    """Register the asyncio mark so `pytest -q` does not warn/fail."""
    config.addinivalue_line(
        "mark", "asyncio: mark a test as an async function"
    )

from core.inference.world_sources import (
    WORLD_FEEDS,
    all_feeds,
    as_tuples,
    feeds_for_region,
    full_text_feeds,
    regions,
)


# ── registry coverage ────────────────────────────────────────────────

def test_registry_covers_all_major_regions():
    expected = {"North America", "Europe", "Middle East", "Asia", "Africa",
                "Latin America", "South Asia", "Oceania"}
    assert expected.issubset(set(regions()))


def test_every_region_has_at_least_one_feed():
    for region, feeds in WORLD_FEEDS.items():
        assert feeds, f"region {region} has no feeds"


def test_all_feeds_are_unique_by_url():
    urls = [s.url for s in all_feeds()]
    assert len(urls) == len(set(urls)), "duplicate feed URLs in registry"


def test_all_feeds_are_unique_by_name():
    names = [s.name for s in all_feeds()]
    assert len(names) == len(set(names)), "duplicate feed names"


def test_feeds_for_region_filters():
    asia = feeds_for_region("Asia")
    assert asia
    assert all(s.region == "Asia" for s in asia)


def test_full_text_feeds_are_marked():
    ft = full_text_feeds()
    assert ft
    assert all(s.full_text for s in ft)
    # NPR is the known full-text source.
    assert any(s.name == "NPR" for s in ft)


def test_as_tuples_shape():
    tuples = as_tuples()
    assert tuples
    for region, name, url in tuples:
        assert region and name and url.startswith("http")


def test_feed_urls_are_https():
    for s in all_feeds():
        assert s.url.startswith("https://"), f"{s.name} is not https"


# ── idempotent registration (against a temp store) ───────────────────

@pytest.fixture
def broadcaster(tmp_path):
    from core.inference.autonomous import AutonomousBroadcaster
    from core.inference.providers import OllamaProvider
    from core.inference.qwen3_tts_http import Qwen3TTSHttpProvider
    from core.inference.runtime import LiveRuntime, RuntimeConfig
    llm = OllamaProvider(model="qwen3:4b")
    tts = Qwen3TTSHttpProvider()
    rt = LiveRuntime(llm, tts, config=RuntimeConfig(max_segments=0))
    return AutonomousBroadcaster(rt, db_path=str(tmp_path / "world.db"))


@pytest.mark.asyncio
async def test_register_source_is_idempotent(broadcaster):
    id1 = await broadcaster.register_source("NPR", "https://feeds.npr.org/1001/rss.xml")
    id2 = await broadcaster.register_source("NPR", "https://feeds.npr.org/1001/rss.xml")
    assert id1 == id2
    sources = await broadcaster.store.list_sources()
    assert len(sources) == 1


@pytest.mark.asyncio
async def test_register_world_sources_idempotent(broadcaster):
    first = await broadcaster.register_world_sources(regions=["Oceania"])
    second = await broadcaster.register_world_sources(regions=["Oceania"])
    assert first == second
    sources = await broadcaster.store.list_sources()
    # No duplicates despite registering twice.
    endpoints = [s.endpoint for s in sources]
    assert len(endpoints) == len(set(endpoints))


@pytest.mark.asyncio
async def test_register_world_sources_subset(broadcaster):
    registered = await broadcaster.register_world_sources(regions=["Africa"])
    sources = await broadcaster.store.list_sources()
    assert len(sources) == len(registered)
    assert len(sources) >= 2
