"""Segment-id allocation, editable feeds, and persisted settings.

Three defects these pin down:

1. ``segment_id`` came from ``f"seg-{segments_produced + 1:03d}"``. The demo,
   one-cycle, and continuous-station paths each run on their own thread but
   share one runtime, so two concurrent runs computed the SAME id; add_segment
   silently overwrote the first, and the second then drove a COMPLETED segment
   to GENERATING — the reported "illegal segment transition completed ->
   generating".
2. The station's feeds were whatever the code registered; there was no way to
   point it at different news sources without editing Python.
3. There was nowhere to change how the station behaves (poll cadence, world
   feeds, dialogue) without editing Python either.
"""

import json
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── segment ids under concurrency ───────────────────────────────────────

class _FakeLLM:
    provider_id = "fake"

    async def health_check(self):
        return True


class _FakeTTS:
    async def health_check(self):
        return True


def _runtime():
    from core.inference.runtime import LiveRuntime, RuntimeConfig
    return LiveRuntime(_FakeLLM(), _FakeTTS(),
                       config=RuntimeConfig(max_segments=0))


def test_segment_ids_are_unique_across_threads():
    """The exact race that produced the illegal transition.

    Eight threads hammering the allocator must never produce a duplicate: a
    duplicate id is what let one run's COMPLETED segment be re-driven by
    another run.
    """
    runtime = _runtime()
    ids, lock = [], threading.Lock()

    def grab():
        got = [runtime._next_segment_id() for _ in range(50)]
        with lock:
            ids.extend(got)

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ids) == 400
    assert len(set(ids)) == 400, "duplicate segment id under concurrency"
    assert runtime.segments_produced == 400


def test_segment_ids_are_sequential_and_padded():
    runtime = _runtime()
    assert runtime._next_segment_id() == "seg-001"
    assert runtime._next_segment_id() == "seg-002"


def test_runtime_serialises_id_allocation_not_the_counter():
    """The counter must match the ids handed out, with no skipped numbers.

    The old code incremented again after playback, so the counter drifted past
    the ids actually in use.
    """
    runtime = _runtime()
    ids = [runtime._next_segment_id() for _ in range(5)]
    assert ids == [f"seg-{i:03d}" for i in range(1, 6)]
    assert runtime.segments_produced == 5


def test_concurrent_runs_cannot_collide_on_a_segment():
    """Two runs sharing a runtime must get different segment ids.

    Reproduces the reported failure directly: without the fix both runs named
    their segment seg-001 and the second raised
    SegmentTransitionError('illegal segment transition completed -> generating').
    """
    from core.broadcast.director import BroadcastDirector
    from core.broadcast.entities import Segment, SegmentStatus

    runtime = _runtime()
    director = BroadcastDirector()
    runtime.director = director

    seen, lock = [], threading.Lock()

    def make_segment():
        sid = runtime._next_segment_id()
        with lock:
            seen.append(sid)
        director.add_segment(Segment(id=sid, status=SegmentStatus.PLANNED,
                                     objective="x"))

    threads = [threading.Thread(target=make_segment) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(seen) == len(set(seen))
    assert len(director.segments) == 10, (
        "add_segment overwrote a segment: two runs shared an id")


# ── settings ────────────────────────────────────────────────────────────

def test_settings_defaults_are_sane():
    from core.inference.settings import StationSettings
    s = StationSettings()
    assert s.poll_seconds > 0
    assert isinstance(s.use_world_feeds, bool)
    assert s.max_compile_per_cycle >= 1


def test_settings_clamp_hostile_values():
    """A zero/negative poll would spin the loop; the floor must hold."""
    from core.inference.settings import MIN_POLL_SECONDS, StationSettings
    s = StationSettings.from_mapping({
        "poll_seconds": 0.5, "max_compile_per_cycle": 99999,
    })
    assert s.poll_seconds == MIN_POLL_SECONDS
    assert s.max_compile_per_cycle <= 100

    s2 = StationSettings.from_mapping({"poll_seconds": "not a number"})
    assert s2.poll_seconds == StationSettings().poll_seconds  # falls back


def test_settings_survive_a_round_trip(tmp_path):
    from core.inference.settings import SettingsStore, StationSettings
    store = SettingsStore(tmp_path / "settings.json")
    store.save(StationSettings(poll_seconds=45.0, use_world_feeds=False,
                               multi_persona=False,
                               max_compile_per_cycle=3))
    loaded = store.load()
    assert loaded.poll_seconds == 45.0
    assert loaded.use_world_feeds is False
    assert loaded.multi_persona is False
    assert loaded.max_compile_per_cycle == 3


def test_settings_preserve_unknown_keys(tmp_path):
    """A newer version's setting must not be dropped by an older one."""
    from core.inference.settings import SettingsStore
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"poll_seconds": 60, "future_option": "keep"}))
    store = SettingsStore(path)
    loaded = store.load()
    assert loaded.extra.get("future_option") == "keep"
    store.save(loaded)
    assert json.loads(path.read_text())["future_option"] == "keep"


def test_malformed_settings_fall_back_to_defaults(tmp_path):
    """A bad edit must never stop the station from starting."""
    from core.inference.settings import SettingsStore
    path = tmp_path / "settings.json"
    for junk in ("not json at all", "[1,2,3]", "null", ""):
        path.write_text(junk)
        settings = SettingsStore(path).load()
        assert settings.poll_seconds > 0

    # A missing file is the same case.
    assert SettingsStore(tmp_path / "absent.json").load().poll_seconds > 0


def test_settings_path_agrees_with_the_console_and_desktop():
    """One resolver, not three.

    The window, the console, and the settings file must agree on the data dir,
    or settings written by one are invisible to another.
    """
    from core.desktop import _project_root
    from core.inference.console import _data_dir
    from core.inference.settings import SettingsStore
    assert _data_dir() == _project_root()
    assert SettingsStore().path.parent == _data_dir()


# ── feeds ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_source_update_and_delete_refuse_to_orphan_articles(tmp_path):
    """Deleting a feed with articles would lose the world model's provenance."""
    from core.models import Article, Source
    from core.persistence import PersistenceError, WorldStore

    store = WorldStore(str(tmp_path / "world.db"))
    sid = await store.add_source(Source(name="Feed", endpoint="https://a/x"))

    # Editing is allowed.
    source = await store.get_source(sid)
    source.name = "Renamed"
    source.enabled = False
    await store.update_source(source)
    reloaded = await store.get_source(sid)
    assert reloaded.name == "Renamed"
    assert reloaded.enabled is False

    # Disabling is the supported way to stop a feed.
    assert await store.set_source_enabled(sid, True) is True
    assert (await store.get_source(sid)).enabled is True

    # Deleting with no articles is fine.
    assert await store.delete_source(sid) is True

    # With an article attached, it must refuse.
    sid2 = await store.add_source(Source(name="Feed2", endpoint="https://b/x"))
    await store.add_article(Article(source_id=sid2, canonical_url="https://b/1",
                                    title="t"))
    with pytest.raises(PersistenceError):
        await store.delete_source(sid2)


def test_console_exposes_feed_and_settings_endpoints():
    """The API surface the settings panel depends on must exist."""
    source = (ROOT / "core" / "inference" / "console.py").read_text()
    for route in ('@app.get("/api/feeds")', '@app.post("/api/feeds")',
                  '@app.patch("/api/feeds/{source_id}")',
                  '@app.delete("/api/feeds/{source_id}")',
                  '@app.get("/api/settings")', '@app.put("/api/settings")'):
        assert route in source, f"missing route: {route}"


def test_ui_has_the_settings_panel():
    html = (ROOT / "core" / "inference" /
            "console_static" / "index.html").read_text()
    js = (ROOT / "core" / "inference" /
          "console_static" / "app.js").read_text()
    assert 'id="settings-overlay"' in html
    assert 'id="feeds-list"' in html
    assert 'id="set-poll"' in html
    # Translucency is the requested look, not a plain dialog.
    css = (ROOT / "core" / "inference" /
           "console_static" / "styles.css").read_text()
    assert "backdrop-filter" in css
    assert "loadFeeds" in js and "saveSettings" in js
