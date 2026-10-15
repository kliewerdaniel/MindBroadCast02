"""Persona voices: map each persona to a distinct TTS profile.

Multi-persona dialogue is only audible as a debate if the voices differ. This
registry maps a persona id to a `VoiceProfile` (speaking pace, and optionally
zero-shot voice-clone reference audio when assets exist).

Two tiers, by what the environment actually supports:

- Pace (always available): Qwen3-TTS accepts a `speed` parameter, so each
  persona speaks at a distinct rate. The skeptic deliberate, the historian
  measured, the analyst brisk. This works today with no assets and makes the
  exchange audibly differentiated.
- Voice cloning (when assets exist): Qwen3-TTS is a zero-shot cloning model,
  so a `reference_audio` + `reference_text` per persona yields a genuinely
  distinct voice. The registry accepts these per persona; until reference
  clips are recorded, personas fall back to pace-only differentiation.

Fail-open on the audio side: an unknown persona gets the neutral presenter
profile (speed 1.0), never a crash. The debate degrades to one pace, it does
not stop.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class VoiceProfile:
    """TTS parameters for one voice.

    `speed` is the always-available differentiator. `reference_audio` /
    `reference_text` enable zero-shot cloning when a clip is present; None
    means pace-only.
    """
    persona_id: str
    speed: float = 1.0
    reference_audio: Optional[str] = None       # path or URL to a ref clip
    reference_text: Optional[str] = None


# Default newsroom voices: distinct paces so the exchange is audible now.
# Speeds chosen to be clearly different but natural (0.85-1.15).
DEFAULT_VOICES: Dict[str, VoiceProfile] = {
    "analyst": VoiceProfile(persona_id="analyst", speed=1.08),
    "skeptic": VoiceProfile(persona_id="skeptic", speed=0.88),
    "historian": VoiceProfile(persona_id="historian", speed=0.96),
    "presenter": VoiceProfile(persona_id="presenter", speed=1.0),
}


class VoiceRegistry:
    """Resolves a persona id to its VoiceProfile, with a neutral fallback."""

    def __init__(self, voices: Optional[Dict[str, VoiceProfile]] = None):
        self.voices: Dict[str, VoiceProfile] = dict(voices or DEFAULT_VOICES)
        self.fallback = VoiceProfile(persona_id="presenter", speed=1.0)

    def register(self, profile: VoiceProfile) -> None:
        self.voices[profile.persona_id] = profile

    def for_persona(self, persona_id: str) -> VoiceProfile:
        return self.voices.get(persona_id, self.fallback)

    def speeds_are_distinct(self, persona_ids: list) -> bool:
        """Whether the given personas are audibly differentiated by pace.

        Used in tests to assert the registry actually differentiates voices
        rather than collapsing everyone to the same speed.
        """
        speeds = {round(self.for_persona(p).speed, 3) for p in persona_ids}
        return len(speeds) > 1
