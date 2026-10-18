"""Persona voices: one consistent voice, differentiated by delivery.

The whole broadcast speaks in one voice — Chris's, cloned zero-shot from
chris.wav. What varies between personas is *delivery* (pace and demeanor), not
identity: the analyst is brisk and direct, the skeptic measured and careful, the
historian warm and unhurried. Consistency of voice is the requirement; delivery
is how the debate stays readable without becoming three different people.

Qwen3-TTS is a zero-shot cloning model, so a single reference clip pins the
timbre for every segment. `instruct` still shapes demeanor, but is now scoped
to delivery ("brisk and direct"), never to a different speaker, so the voice
stays Chris throughout.
"""

import os
from dataclasses import dataclass

# The single voice the station speaks in. A zero-shot reference clip pins the
# timbre for every segment, across personas and stories.
_HERE = os.path.dirname(os.path.abspath(__file__))          # core/inference


def _candidate_clip_dirs() -> list[str]:
    """Everywhere chris.wav may legitimately live, best first.

    A frozen one-file build extracts to an ephemeral temp dir, so resolving the
    clip from ``__file__`` pointed at a path that is deleted on quit and never
    contained the clip to begin with — the station silently lost its voice and
    fell back to a synthetic one. The clip is bundled into the app, but the
    user's own copy (which they can replace) must win, so the data dir comes
    first.
    """
    import sys

    dirs: list[str] = []

    # 1. The user's data dir — where a replaced/updated clip belongs, and the
    #    only writable location for a frozen .app inside a read-only DMG.
    dirs.append(os.path.join(os.path.expanduser("~"), ".mindbroadcast"))
    dirs.append(os.path.join(os.path.expanduser("~"), ".mindbroadcast", "data"))

    if getattr(sys, "frozen", False):
        # 2. Beside the executable (a user may drop the clip next to the app).
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        dirs.append(exe_dir)
        dirs.append(os.path.join(exe_dir, "..", "Resources"))
        # 3. The bundled copy, inside PyInstaller's extraction dir. sys._MEIPASS
        #    is the documented root for bundled data.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(meipass)
            dirs.append(os.path.join(meipass, "core", "inference"))
    else:
        # 2. A source run: the repo root, as before.
        dirs.append(os.path.dirname(os.path.dirname(_HERE)))

    return dirs


def _resolve_chris_clip() -> str:
    """The path the station's voice is pinned to.

    Returns the first existing chris.wav, or the conventional project-root path
    when none exists (so error messages name a real location rather than None).
    """
    for directory in _candidate_clip_dirs():
        candidate = os.path.join(directory, "chris.wav")
        if os.path.isfile(candidate):
            return candidate
    # Nothing found: name the most likely place the user should put it.
    return os.path.join(_candidate_clip_dirs()[0], "chris.wav")


_CHRIS_CLIP = _resolve_chris_clip()

# Transcript of chris.wav — its own words, so cloning conditions on what the
# clip actually says. If unknown, leave None; the model clones from audio.
CHRIS_REFERENCE_TEXT = None


@dataclass
class VoiceProfile:
    """TTS parameters for one voice.

    `speed` is pacing. `reference_audio` / `reference_text` pin the cloned
    timbre (chris.wav for the whole station). `instruct` shapes delivery
    demeanor only — it must not request a different speaker, so the voice
    stays consistent.
    """
    persona_id: str
    speed: float = 1.0
    reference_audio: str | None = None       # path or URL to a ref clip
    reference_text: str | None = None
    instruct: str | None = None


def chris_voice(
    persona_id: str,
    speed: float = 1.0,
    instruct: str | None = None,
) -> VoiceProfile:
    """A profile speaking in Chris's voice, with the given delivery.

    This is the factory every persona uses: the reference clip is fixed to
    chris.wav so the timbre is constant; only speed and demeanor vary.
    """
    return VoiceProfile(
        persona_id=persona_id,
        speed=speed,
        reference_audio=_CHRIS_CLIP,
        reference_text=CHRIS_REFERENCE_TEXT,
        instruct=instruct,
    )


# Default newsroom voices: all Chris, differing only in delivery. Demeanor is
# scoped to pacing/tone so it never asks for a different speaker.
DEFAULT_VOICES: dict[str, VoiceProfile] = {
    "analyst": chris_voice(
        "analyst", speed=1.08,
        instruct="Brisk, direct, and confident news delivery."),
    "skeptic": chris_voice(
        "skeptic", speed=0.90,
        instruct="Measured and careful, pausing to weigh each point."),
    "historian": chris_voice(
        "historian", speed=0.96,
        instruct="Warm and unhurried, like telling a story."),
    "presenter": chris_voice("presenter", speed=1.0),
}


class VoiceRegistry:
    """Resolves a persona id to its VoiceProfile, with a neutral fallback.

    Every persona speaks the same cloned voice; the fallback is Chris too, so
    an unknown persona never produces a different timbre or a crash.
    """

    def __init__(self, voices: dict[str, VoiceProfile] | None = None):
        self.voices: dict[str, VoiceProfile] = dict(voices or DEFAULT_VOICES)
        self.fallback = chris_voice("presenter", speed=1.0)

    def register(self, profile: VoiceProfile) -> None:
        self.voices[profile.persona_id] = profile

    def for_persona(self, persona_id: str) -> VoiceProfile:
        return self.voices.get(persona_id, self.fallback)

    def chris_clip_path(self) -> str | None:
        """The reference clip pinning the station's voice, if it exists."""
        return _CHRIS_CLIP if os.path.exists(_CHRIS_CLIP) else None

    def delivery_is_varied(self, persona_ids: list[str]) -> bool:
        """Whether the personas differ in delivery (pace or demeanor).

        Delivery variety keeps the debate readable; the voice itself stays
        constant. Used in tests to assert differentiation without identity.
        """
        profiles = [self.for_persona(p) for p in persona_ids]
        speeds = {round(p.speed, 3) for p in profiles}
        demeanors = {p.instruct for p in profiles if p.instruct}
        return len(speeds) > 1 or len(demeanors) > 1

    def all_use_chris_voice(self, persona_ids: list[str]) -> bool:
        """Whether every persona is pinned to the same reference clip.

        The consistency guarantee: one voice for the whole station.
        """
        clips = {self.for_persona(p).reference_audio for p in persona_ids}
        return len(clips) == 1 and self.chris_clip_path() in clips
