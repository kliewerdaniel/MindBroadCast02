"""The station's voice, the frozen clip path, and cycle progress reporting.

Three defects these pin down:

1. The cloned voice (chris.wav) resolved from ``__file__``, which in a frozen
   one-file build points into an ephemeral temp dir. The clip was never there,
   so the packaged app silently spoke in a different voice.
2. The app's window disabled text selection (pywebview's default), so nothing
   in the UI — including the transcript — could be selected or copied.
3. A cycle takes minutes and reported nothing in between, so a working station
   and a hung one looked identical.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── the voice ───────────────────────────────────────────────────────────

def test_chris_clip_exists_and_is_a_real_recording():
    """The station's voice is a real clip, not a placeholder."""
    import wave

    clip = ROOT / "chris.wav"
    assert clip.is_file(), "chris.wav is the station's voice and must exist"
    with wave.open(str(clip)) as handle:
        assert handle.getnframes() > 0
        assert handle.getframerate() > 8000
        assert handle.getnframes() / handle.getframerate() > 1.0


def test_voice_resolves_to_the_clip_in_source_mode():
    from core.inference.voices import VoiceRegistry, _CHRIS_CLIP
    assert os.path.isfile(_CHRIS_CLIP), _CHRIS_CLIP
    assert VoiceRegistry().chris_clip_path() == _CHRIS_CLIP
    assert VoiceRegistry().all_use_chris_voice(
        ["analyst", "skeptic", "historian", "presenter"])


def test_frozen_voice_never_resolves_into_the_extraction_dir(monkeypatch):
    """A frozen build must not look for the clip only in _MEIPASS.

    _MEIPASS is deleted on quit, so a clip resolved from there is a path that
    cannot be the user's own voice file. The data dir must come first, and the
    result must never be a bare temp path when the user has a real clip.
    """
    import importlib

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "/tmp/_MEI_fake", raising=False)
    try:
        import core.inference.voices as voices
        importlib.reload(voices)
        candidates = voices._candidate_clip_dirs()
        # The user's data dir is searched first — it is where a replaced clip
        # belongs and the only writable location for an app inside a DMG.
        assert candidates[0] == os.path.join(os.path.expanduser("~"),
                                             ".mindbroadcast")
        # The bundled copy is still searched, so the release ships a voice.
        assert "/tmp/_MEI_fake" in candidates
        # And a real clip wins over the temp dir when one exists.
        home_clip = Path(candidates[0]) / "chris.wav"
        if home_clip.is_file():
            assert voices._CHRIS_CLIP == str(home_clip)
    finally:
        monkeypatch.undo()
        importlib.reload(sys.modules["core.inference.voices"])


def test_release_bundles_the_voice_clip():
    """The frozen app must carry chris.wav, or it ships without its voice."""
    script = (ROOT / "packaging" / "release.sh").read_text()
    assert script.count('--add-data "chris.wav') == 2, (
        "chris.wav must be bundled for both the macOS and Windows builds")


# ── text selection ──────────────────────────────────────────────────────

def test_desktop_window_allows_text_selection():
    """pywebview defaults text_select to False, injecting user-select: none.

    That made every word in the app unselectable, including the transcript.
    """
    source = (ROOT / "core" / "desktop.py").read_text()
    assert "text_select=True" in source, (
        "the window must allow text selection; the default disables it")


# ── progress reporting ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cycle_reports_its_phases():
    """A cycle must emit progress as it moves, not only when it finishes."""
    from core.inference.autonomous import AutonomousBroadcaster

    events = []

    class _Runtime:
        errors: list = []

        async def _produce_segment(self, topic):
            return None

    broadcaster = AutonomousBroadcaster(
        runtime=_Runtime(), db_path=":memory:")
    broadcaster.on_progress = events.append

    # No source registered: the cycle still reports that it started and why it
    # stopped, so the listener is never left with silence.
    await broadcaster.run_cycle(source_id=None)

    phases = [e["phase"] for e in events]
    assert "starting" in phases
    assert phases, "a cycle emitted no progress at all"


@pytest.mark.asyncio
async def test_progress_callback_failure_never_breaks_the_cycle():
    """Progress is diagnostics; it must never abort the broadcast."""
    from core.inference.autonomous import AutonomousBroadcaster

    class _Runtime:
        errors: list = []

        async def _produce_segment(self, topic):
            return None

    def explode(_event):
        raise RuntimeError("the UI callback blew up")

    broadcaster = AutonomousBroadcaster(
        runtime=_Runtime(), db_path=":memory:")
    broadcaster.on_progress = explode

    cycle = await broadcaster.run_cycle(source_id=None)   # must not raise
    assert cycle is not None


def test_progress_events_reach_the_console_stream():
    """The console must turn progress into SSE events and remember the phase."""
    source = (ROOT / "core" / "inference" / "console.py").read_text()
    assert "_on_station_progress" in source
    assert 'payload["type"] = "progress"' in source
    # The callback must be attached to the broadcaster the station runs.
    assert "state.autonomous.on_progress = _on_station_progress" in source
    # /api/station must report the phase so a late joiner sees it.
    assert '"phase": last.get("phase", "")' in source


def test_ui_renders_progress_and_the_voice():
    """The console must show the activity feed and which voice is speaking."""
    js = (ROOT / "core" / "inference" / "console_static" / "app.js").read_text()
    html = (ROOT / "core" / "inference" / "console_static" / "index.html").read_text()

    assert "onProgress" in js
    assert 'case "progress"' in js
    assert "activity-log" in html
    assert 'id="activity-phase"' in html
    # The voice indicator: a lost voice must be visible, not silent.
    assert 'id="voice-text"' in html
    assert "cloned_voice" in js
