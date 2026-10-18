"""Tests for the consistent station voice: one Chris clone, varied delivery.

The invariant flipped from "distinct voices per persona" to "one consistent
voice (Chris), differentiated only by delivery." These tests assert the
consistency guarantee and that delivery still varies enough to keep the debate
readable.
"""

import os

from core.inference.runtime import LiveRuntime
from core.inference.voices import (
    DEFAULT_VOICES,
    VoiceProfile,
    VoiceRegistry,
    chris_voice,
)


def _registry():
    return VoiceRegistry()


# ── the consistency guarantee ────────────────────────────────────────

def test_chris_clip_exists():
    reg = _registry()
    path = reg.chris_clip_path()
    assert path is not None, "chris.wav not found at project root"
    assert os.path.exists(path)


def test_all_personas_use_the_same_voice():
    reg = _registry()
    assert reg.all_use_chris_voice(["analyst", "skeptic", "historian"])


def test_presenter_also_chris():
    reg = _registry()
    assert reg.for_persona("presenter").reference_audio == reg.chris_clip_path()


def test_unknown_persona_falls_back_to_chris():
    reg = _registry()
    v = reg.for_persona("nobody")
    assert v.reference_audio == reg.chris_clip_path()
    assert v.speed == 1.0


def test_chris_voice_factory_sets_reference():
    v = chris_voice("x", speed=1.0)
    assert v.reference_audio == _registry().chris_clip_path()


# ── delivery varies, identity does not ───────────────────────────────

def test_delivery_is_varied():
    reg = _registry()
    assert reg.delivery_is_varied(["analyst", "skeptic", "historian"])


def test_demeanor_never_requests_a_different_speaker():
    """instruct scopes to delivery, never identity — keeps the voice Chris."""
    reg = _registry()
    # None of the demeanors should describe a different person/voice.
    banned = ("woman", "man ", "girl", "boy", "another", "different voice",
              "female", "male")
    for pid in ("analyst", "skeptic", "historian", "presenter"):
        instr = (reg.for_persona(pid).instruct or "").lower()
        for word in banned:
            assert word not in instr, (
                f"{pid} demeanor requests a different speaker: {instr!r}")


def test_personas_share_one_reference_clip():
    reg = _registry()
    clips = {reg.for_persona(p).reference_audio
             for p in ("analyst", "skeptic", "historian")}
    assert len(clips) == 1


# ── registration still works ─────────────────────────────────────────

def test_register_overrides():
    reg = _registry()
    reg.register(chris_voice("analyst", speed=0.5))
    assert reg.for_persona("analyst").speed == 0.5
    # Still the same voice.
    assert reg.all_use_chris_voice(["analyst"])


def test_voice_profile_carries_clone_reference():
    reg = _registry()
    reg.register(VoiceProfile(
        persona_id="analyst", speed=1.0,
        reference_audio="/voices/analyst.wav",
        reference_text="a sample line"))
    v = reg.for_persona("analyst")
    assert v.reference_audio == "/voices/analyst.wav"
    assert v.reference_text == "a sample line"


# ── per-speaker grouping ─────────────────────────────────────────────

class _Turn:
    def __init__(self, persona_id, text):
        self.persona_id = persona_id
        self.text = text


def test_group_collapses_consecutive_same_speaker():
    turns = [_Turn("analyst", "First point."),
             _Turn("analyst", "Second point."),
             _Turn("skeptic", "But wait.")]
    groups = LiveRuntime._group_by_speaker(turns)
    assert groups == [
        ("analyst", "First point. Second point."),
        ("skeptic", "But wait."),
    ]


def test_group_alternating_speakers_stay_separate():
    turns = [_Turn("analyst", "A."),
             _Turn("skeptic", "B."),
             _Turn("analyst", "C.")]
    groups = LiveRuntime._group_by_speaker(turns)
    assert [g[0] for g in groups] == ["analyst", "skeptic", "analyst"]


def test_group_skips_empty_turns():
    turns = [_Turn("analyst", "A."), _Turn("analyst", "  "),
             _Turn("skeptic", "B.")]
    groups = LiveRuntime._group_by_speaker(turns)
    assert groups == [("analyst", "A."), ("skeptic", "B.")]


def test_group_defaults_missing_persona():
    turns = [_Turn("", "Someone said this.")]
    groups = LiveRuntime._group_by_speaker(turns)
    assert groups == [("presenter", "Someone said this.")]
