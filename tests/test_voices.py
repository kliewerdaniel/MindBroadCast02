"""Tests for persona voices: distinct paces, per-speaker synthesis grouping.

The invariant: a multi-persona debate must be audibly a debate. Voices differ
by pace now (always available) and by zero-shot clone when reference audio
exists. An unknown persona degrades to the neutral presenter, never a crash.
"""

from core.inference.runtime import LiveRuntime
from core.inference.voices import DEFAULT_VOICES, VoiceProfile, VoiceRegistry


# ── VoiceRegistry ────────────────────────────────────────────────────

def test_default_voices_are_distinct_by_pace():
    reg = VoiceRegistry()
    assert reg.speeds_are_distinct(["analyst", "skeptic", "historian"])


def test_for_persona_returns_configured_voice():
    reg = VoiceRegistry()
    assert reg.for_persona("skeptic").speed < 1.0     # deliberate
    assert reg.for_persona("analyst").speed > 1.0     # brisk


def test_unknown_persona_gets_neutral_fallback():
    reg = VoiceRegistry()
    voice = reg.for_persona("nobody")
    assert voice.speed == 1.0
    assert voice.persona_id == "presenter"


def test_register_overrides():
    reg = VoiceRegistry()
    reg.register(VoiceProfile(persona_id="analyst", speed=0.5))
    assert reg.for_persona("analyst").speed == 0.5


def test_voice_profile_carries_clone_reference():
    reg = VoiceRegistry()
    reg.register(VoiceProfile(
        persona_id="analyst", speed=1.0,
        reference_audio="/voices/analyst.wav",
        reference_text="a sample line"))
    v = reg.for_persona("analyst")
    assert v.reference_audio == "/voices/analyst.wav"
    assert v.reference_text == "a sample line"


def test_default_personas_carry_voice_instructions():
    """Each persona has an `instruct` describing a distinct timbre.

    Qwen3-TTS's instruct param shapes voice from a description — genuine
    variation with no reference recording (the -Base checkpoint can't do
    generate_voice_design, but instruct works).
    """
    reg = VoiceRegistry()
    for pid in ("analyst", "skeptic", "historian"):
        assert reg.for_persona(pid).instruct
    # The presenter is the neutral voice — no instruction.
    assert reg.for_persona("presenter").instruct is None
    # The three personas have distinct instructions.
    instrs = {reg.for_persona(p).instruct
              for p in ("analyst", "skeptic", "historian")}
    assert len(instrs) == 3


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
