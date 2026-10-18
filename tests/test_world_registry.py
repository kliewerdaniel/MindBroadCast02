"""The expanded world registry, and region-aware rotation.

The registry grew from 25 feeds to ~176, all probed live before being added.
Two things must stay true, and both are easy to break silently:

1. Every URL is a real, HTTPS, parseable feed. A dead or cleartext feed that
   ships in the registry fails at fetch time, per cycle, forever.
2. Rotation interleaves regions. The registry holds far more North American
   feeds than Oceanian ones, so registry order would make a listener hear one
   region for dozens of cycles before another appeared.
"""

import collections
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _feeds():
    from core.inference.world_sources import all_feeds
    return all_feeds()


def test_registry_has_at_least_150_feeds():
    """The registry was expanded deliberately; a regression that drops feeds
    back to the original 25 should be loud."""
    feeds = _feeds()
    assert len(feeds) >= 150, f"only {len(feeds)} feeds"
    assert len(feeds) == len({f.url for f in feeds}), "duplicate feed URLs"


def test_every_feed_is_https():
    """Cleartext feeds are refused: the registry is HTTPS-only."""
    bad = [f for f in _feeds() if not f.url.startswith("https://")]
    assert not bad, f"non-https feeds: {[(f.name, f.url) for f in bad]}"


def test_every_feed_has_a_region_name_and_url():
    for feed in _feeds():
        assert feed.region.strip()
        assert feed.name.strip()
        assert feed.url.startswith("https://")


def test_every_region_has_feeds():
    from core.inference.world_sources import WORLD_FEEDS
    for region, feeds in WORLD_FEEDS.items():
        assert feeds, f"region {region} has no feeds"


def test_all_eight_world_regions_are_still_covered():
    """The original coverage requirement: not one or two Anglophone outlets."""
    from core.inference.world_sources import WORLD_FEEDS
    for region in ("North America", "Europe", "Middle East", "Asia", "Africa",
                   "Latin America", "South Asia", "Oceania"):
        assert WORLD_FEEDS.get(region), f"region {region} lost its feeds"


def test_region_of_maps_urls_and_returns_empty_for_unknown():
    from core.inference.world_sources import all_feeds, region_of
    sample = all_feeds()[0]
    assert region_of(sample.url) == sample.region
    assert region_of("https://example.com/not-in-registry.xml") == ""


def test_rotation_keeps_every_url_and_invents_none():
    from core.inference.world_sources import all_feeds, rotation_order
    urls = [f.url for f in all_feeds()]
    order = rotation_order(urls)
    assert len(order) == len(set(urls))
    assert set(order) == set(urls), "rotation dropped or invented a feed"


def test_rotation_interleaves_regions():
    """Consecutive cycles must not all come from one region.

    Measured as the longest run of the same region. In registry order that run
    is the size of the largest region (~48 cycles); interleaved it is bounded
    by the smallest region's feed count.
    """
    from core.inference.world_sources import all_feeds, region_of, rotation_order

    urls = [f.url for f in all_feeds()]
    registry_regions = [region_of(u) for u in urls]
    rotated_regions = [region_of(u) for u in rotation_order(urls)]

    def longest_run(seq):
        best = current = 1
        for a, b in zip(seq, seq[1:]):
            current = current + 1 if a == b else 1
            best = max(best, current)
        return best

    assert longest_run(rotated_regions) < longest_run(registry_regions)
    # Concretely: no region should dominate a stretch of cycles. The smallest
    # region has 6 feeds, so a run longer than that means the interleave broke.
    smallest = min(collections.Counter(registry_regions).values())
    assert longest_run(rotated_regions) <= smallest, (
        f"longest same-region run is {longest_run(rotated_regions)}, "
        f"expected <= {smallest}")


def test_rotation_covers_many_regions_in_a_short_window():
    """A 9-cycle window should reach most of the 9 regions, not one of them."""
    from core.inference.world_sources import all_feeds, region_of, rotation_order
    urls = [f.url for f in all_feeds()]
    rotated = [region_of(u) for u in rotation_order(urls)]
    window = rotated[:9]
    assert len(set(window)) >= 7, f"only {len(set(window))} regions in 9 cycles"


def test_rotation_puts_user_feeds_first_and_keeps_them():
    """A feed the user added is not a world feed and must survive rotation."""
    from core.inference.world_sources import all_feeds, rotation_order
    mine = "https://example.com/my-own-feed.xml"
    urls = [mine] + [f.url for f in all_feeds()]
    order = rotation_order(urls)
    assert order[0] == mine
    assert mine in order
    assert set(order) == set(urls)


def test_rotation_is_deterministic():
    from core.inference.world_sources import all_feeds, rotation_order
    urls = [f.url for f in all_feeds()]
    assert rotation_order(urls) == rotation_order(urls)


@pytest.mark.asyncio
async def test_pick_source_rotates_across_regions(tmp_path):
    """The broadcaster's own selection must use the interleaved order."""
    from core.inference.autonomous import AutonomousBroadcaster
    from core.inference.world_sources import all_feeds, region_of

    class _Runtime:
        errors: list = []

    broadcaster = AutonomousBroadcaster(runtime=_Runtime(),
                                       db_path=":memory:")
    await broadcaster.register_world_sources()

    seen_regions = []
    for _ in range(9):
        source = await broadcaster._pick_source(None)
        assert source is not None
        seen_regions.append(region_of(source.endpoint))

    # Nine consecutive picks should span most regions, not one.
    assert len(set(seen_regions)) >= 7, (
        f"9 consecutive cycles hit only {set(seen_regions)}")


def test_settings_default_poll_is_thirty_seconds():
    from core.inference.settings import MIN_POLL_SECONDS, StationSettings
    assert StationSettings().poll_seconds == 30.0
    assert MIN_POLL_SECONDS == 30.0


def test_world_info_endpoint_shape():
    """The UI reads the region list from the server rather than hardcoding it."""
    source = (ROOT / "core" / "inference" / "console.py").read_text()
    assert '@app.get("/api/world")' in source
    assert '"regions": list(WORLD_FEEDS.keys())' in source

    js = (ROOT / "core" / "inference" /
          "console_static" / "app.js").read_text()
    assert "loadWorld" in js
    # The hardcoded list must be gone, or it drifts again.
    assert 'const REGIONS = ["North America"' not in js


def test_readme_feed_count_matches_the_registry():
    """A stale count in the README is a claim the code no longer supports.

    It has already drifted once (25 feeds / 8 regions after the registry grew
    to 176 / 9), so this keeps the two in step mechanically.
    """
    from core.inference.world_sources import WORLD_FEEDS, all_feeds

    readme = (ROOT / "README.md").read_text()
    total = len(all_feeds())
    regions = len(WORLD_FEEDS)

    assert f"**{total} sources across {regions} world regions**" in readme, (
        f"README does not say '{total} sources across {regions} world "
        f"regions'")
    assert f"({total} feeds)" in readme, (
        f"the pipeline diagram does not say '({total} feeds)'")


def test_settings_default_is_documented_as_thirty_seconds():
    """The UI's hint text and the default must agree."""
    from core.inference.settings import MIN_POLL_SECONDS, StationSettings
    assert StationSettings().poll_seconds == MIN_POLL_SECONDS

    js = (ROOT / "core" / "inference" /
          "console_static" / "app.js").read_text()
    # The panel must not fall back to the old 120s when a value is missing.
    assert "s.poll_seconds ?? 120" not in js
    assert "poll_seconds: 30" in js
