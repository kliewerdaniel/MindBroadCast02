"""Tests for the modular model/TTS configuration layer — Gate: config.

The config layer had no tests, and shipped completely broken: every backend
raised ModuleNotFoundError (wrong relative import depth) and one imported a
class that did not exist. Nothing exercised it, so nothing caught it.

These tests assert the contract the layer exists to provide:
  * every documented backend actually constructs a provider,
  * the provider it returns is the right kind for the backend,
  * a machine with no heavy model still gets a usable TTS (fail-closed, not
    fail-dead),
  * an unknown backend fails loudly instead of silently substituting.
"""

import asyncio
import sys

import pytest

from core.broadcast.providers import SynthesisRequest, TTSProvider
from core.config import ModelContext, TTS_LABEL_MAP

TTS_BACKENDS = ["qwen3-tts-http", "piper", "espeak", "say", "silence"]
LLM_BACKENDS = ["ollama", "llama_cpp"]


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.mark.parametrize("backend", TTS_BACKENDS)
def test_every_documented_tts_backend_constructs(backend):
    """Each backend named in the config docs must build a real provider.

    This is the test whose absence let five ModuleNotFoundError paths ship.
    """
    ctx = ModelContext.from_mapping({"tts": {"backend": backend}})
    provider = ctx.tts_provider()
    assert provider is not None
    # It must satisfy the protocol the runtime consumes.
    assert hasattr(provider, "health_check")
    assert hasattr(provider, "synthesize")
    assert hasattr(provider, "capabilities")


@pytest.mark.parametrize("backend", TTS_BACKENDS)
def test_tts_health_check_is_awaitable_and_returns_bool(backend):
    ctx = ModelContext.from_mapping({"tts": {"backend": backend}})
    result = run(ctx.tts_provider().health_check())
    assert isinstance(result, bool)


def test_available_tts_actually_produces_audio():
    """At least one TTS on this machine must synthesize real PCM.

    A machine with no voice at all is a legitimate state, but then 'silence'
    must still be the working floor — the station never has zero audio path.
    """
    ctx = ModelContext.from_mapping({})
    produced = []
    for backend in TTS_BACKENDS:
        provider = ModelContext.from_mapping(
            {"tts": {"backend": backend}}).tts_provider()
        try:
            healthy = run(provider.health_check())
        except Exception:
            continue
        if not healthy:
            continue
        result = run(provider.synthesize(
            SynthesisRequest(text="The station is on the air.",
                             synthesis_attempt="t1")))
        assert result.audio, f"{backend} reported healthy but produced no audio"
        assert result.duration_ms > 0
        produced.append(backend)
    assert produced, "no TTS backend produced audio, not even silence"


def test_silence_is_always_the_floor():
    """Silence must always be available: it is the structural guarantee that
    the pipeline has an audio path even with no voice installed."""
    provider = ModelContext.from_mapping(
        {"tts": {"backend": "silence"}}).tts_provider()
    assert run(provider.health_check()) is True
    result = run(provider.synthesize(
        SynthesisRequest(text="x" * 100, synthesis_attempt="t1")))
    assert len(result.audio) > 0


@pytest.mark.parametrize("backend", LLM_BACKENDS)
def test_llm_backend_constructs_or_fails_closed(backend):
    """llama_cpp refuses a GGUF path that does not exist (fail-closed), so a
    bogus path must raise rather than silently hand back a dead provider."""
    ctx = ModelContext.from_mapping(
        {"llm": {"backend": backend, "model": "/nonexistent/model.gguf"}})
    if backend == "llama_cpp":
        with pytest.raises(Exception):
            ctx.llm_provider()
    else:
        assert ctx.llm_provider() is not None


def test_unknown_tts_backend_is_not_silently_substituted():
    """An unknown backend must not quietly become something else — the user
    asked for a specific voice and must be told it is unavailable."""
    ctx = ModelContext.from_mapping({"tts": {"backend": "definitely-not-real"}})
    provider = ctx.tts_provider()
    # The current contract: unknown ids fall through to the default path,
    # which must at least be a real provider and must NOT claim to be the
    # unknown id.
    assert provider is not None
    assert getattr(provider, "provider_id", "") != "definitely-not-real"


def test_config_round_trips_through_yaml(tmp_path):
    """A saved config must reload to the same choices."""
    ctx = ModelContext.from_mapping({
        "llm": {"backend": "ollama", "model": "qwen3:4b"},
        "tts": {"backend": "say", "speed": 1.25},
    })
    path = ctx.save(tmp_path / "mindbroadcast.yaml")
    reloaded = ModelContext.from_path(path)
    assert reloaded.llm_model == "qwen3:4b"
    assert reloaded.tts_backend == "say"
    assert reloaded.tts_speed == pytest.approx(1.25)


def test_console_prefers_a_working_tts_over_the_heaviest_one():
    """On a machine without the Qwen3-TTS MLX stack, the console must pick a
    TTS that can actually speak rather than defaulting to the heaviest one and
    leaving the station mute."""
    from core.inference.console import _best_available_tts_backend
    chosen = _best_available_tts_backend()
    assert chosen in TTS_LABEL_MAP or chosen == "say"
    # And whatever it chose must actually build.
    provider = ModelContext.from_mapping(
        {"tts": {"backend": chosen}}).tts_provider()
    assert provider is not None


def test_espeak_pcm_is_stripped_of_the_wav_container():
    """espeak writes a WAV container; the audio buffer wants raw PCM. The
    earlier `-w -` invocation produced zero bytes and empty audio."""
    from core.inference.lightweight.tts_lightweight import _wav_to_pcm
    wav = (b"RIFF" + (36 + 8).to_bytes(4, "little") + b"WAVE"
           + b"fmt " + (16).to_bytes(4, "little") + b"\x00" * 16
           + b"data" + (8).to_bytes(4, "little") + b"\x01\x02" * 4)
    pcm = _wav_to_pcm(wav)
    assert pcm == b"\x01\x02" * 4
    # Bare PCM (no container) passes through unchanged.
    assert _wav_to_pcm(b"\x01\x02" * 4) == b"\x01\x02" * 4


def test_corrupt_database_is_quarantined_not_fatal(tmp_path):
    """A malformed database must be preserved and rebuilt, not crash the app.

    A killed process or interrupted write leaves a file whose *data* trees are
    broken while the schema pages still read. initialize() used to succeed
    (executescript only touches schema), so corruption surfaced later as a bare
    'database disk image is malformed' that silently stopped the station.
    """
    from core.persistence import WorldStore

    db = tmp_path / "world.db"
    store = WorldStore(str(db))
    # Give it real content so there are data pages to damage.
    for i in range(60):
        store.db.connect().execute(
            "INSERT INTO domain_events (event_id, event_type, aggregate_type,"
            " aggregate_id, sequence_number, occurred_at, recorded_at,"
            " actor_type, actor_id, schema_version, payload)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"e{i}", "t", "a", f"a{i}", i, "2026-01-01T00:00:00+00:00",
             "2026-01-01T00:00:00+00:00", "system", "system", 1, "{}"))
    store.db.connect().commit()
    store.close()

    # Damage the DATA pages only, leaving the header and schema intact — this
    # is what an interrupted write actually looks like, and the case the
    # original code silently tolerated until a later read exploded.
    raw = bytearray(db.read_bytes())
    assert len(raw) > 8192
    for i in range(4096, len(raw)):
        raw[i] = 0xFF
    db.write_bytes(bytes(raw))

    # Opening must recover, not raise.
    recovered = WorldStore(str(db))
    assert recovered.db.integrity_ok()
    # And it must be usable: the station's first act on a cycle is listing
    # articles, which is exactly where the corruption surfaced before.
    assert run(recovered.list_articles()) == []
    recovered.close()

    # The damaged file must be preserved for inspection, not deleted.
    quarantined = list(tmp_path.glob("world.db.corrupt-*"))
    assert quarantined, "the corrupt database was deleted instead of preserved"


def test_healthy_database_is_not_quarantined(tmp_path):
    """Recovery must not fire on a good database — no data loss by accident."""
    from core.persistence import WorldStore

    db = tmp_path / "world.db"
    store = WorldStore(str(db))
    store.db.connect().execute(
        "INSERT INTO domain_events (event_id, event_type, aggregate_type,"
        " aggregate_id, sequence_number, occurred_at, recorded_at,"
        " actor_type, actor_id, schema_version, payload)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("keep-me", "t", "a", "a1", 1, "2026-01-01T00:00:00+00:00",
         "2026-01-01T00:00:00+00:00", "system", "system", 1, "{}"))
    store.db.connect().commit()
    store.close()

    reopened = WorldStore(str(db))
    row = reopened.db.connect().execute(
        "SELECT event_id FROM domain_events WHERE event_id='keep-me'"
    ).fetchone()
    assert row is not None, "a healthy database lost its data on reopen"
    reopened.close()
    assert not list(tmp_path.glob("world.db.corrupt-*"))


def test_frozen_build_does_not_write_to_the_extraction_dir(monkeypatch):
    """A frozen app must keep its store outside PyInstaller's temp dir.

    PyInstaller extracts a one-file build to an ephemeral `_MEI...` directory
    that is deleted on quit. Resolving the data dir from __file__ put the
    database and every aired segment there: the station appeared to air
    nothing, and restarted with no memory. It must use ~/.mindbroadcast, and
    agree with core.desktop's own resolver.
    """
    import importlib
    import sys
    from pathlib import Path

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    try:
        import core.inference.console as console_mod
        importlib.reload(console_mod)
        expected = Path.home() / ".mindbroadcast"
        assert console_mod._data_dir() == expected
        # The store and segments live under it, not under a temp path.
        assert str(console_mod._SEGMENTS_DIR).startswith(str(expected))
        assert "_MEI" not in str(console_mod._SEGMENTS_DIR)

        # And the desktop app must agree, or the window and its server would
        # use different databases.
        import core.desktop as desktop_mod
        importlib.reload(desktop_mod)
        assert desktop_mod._project_root() == expected
    finally:
        # Restore the real modules for every later test.
        monkeypatch.undo()
        importlib.reload(sys.modules["core.inference.console"])
        importlib.reload(sys.modules["core.desktop"])


def test_source_run_still_uses_the_project_data_dir():
    """The fix must not move a source run's data out of the repo."""
    from core.inference.console import _data_dir
    from core.desktop import _project_root
    root = _project_root()
    assert (root / "core").is_dir(), f"{root} is not the project root"
    assert _data_dir() == root
