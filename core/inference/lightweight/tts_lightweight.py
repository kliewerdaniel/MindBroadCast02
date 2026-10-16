"""Lightweight TTS backends.

These are the real, cheap, natural-leaning alternatives to the Qwen3-TTS MLX
server. Each implements the same TTSProvider protocol, so the runtime can swap
them without any change to the audio buffer or the broadcast director.

Trade-offs the user should see, honestly:
  * qwen3-tts-http  — best voice, persistent server + MLX, needs ~3GB model
                       and a dedicated port. The default, but the heaviest.
  * piper            — neural, ~200ms, lightweight, cross-platform. Smaller
                       footprint than MLX. Prefer this when RAM is tight.
  * espeak           — ultra-light, system speech. Mechanical. Use when
                       nothing else is available or the user explicitly wants
                       it. Never a voice quality claim.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from ...broadcast.providers import (
    ProviderCapabilities,
    ProviderError,
    SynthesisRequest,
    SynthesisResult,
    TTSProvider,
)


@dataclass
class PiperTTSProvider(TTSProvider):
    """Neural TTS via Piper. Clean, small, real voice, ~200ms synthesis.

    Piper is the honest light-weight neighbour of Qwen3-TTS: it is a neural
    engine, not a placeholder. Small voice files (~a few MB per language) and
    synthesis is ~200ms on a modern CPU, which is plenty for a 30-90s segment.
    """

    provider_id: str = "piper"

    def __init__(
        self,
        voice_file: Optional[str] = None,
        language: str = "en-us",
        speed: float = 1.0,
        *,
        sample_rate: int = 24000,
    ):
        self.sample_rate = sample_rate
        self.speed = speed
        self.language = language
        self._voice_file = voice_file or f"piper-{language}-default"
        self._capabilities = ProviderCapabilities(
            streaming=False,
            notes=f"Piper neural TTS, voice={self._voice_file!r}",
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def health_check(self) -> bool:
        """The piper binary must exist and its voice file must be readable."""
        if shutil_which("piper") is None:
            return False
        import os
        if not os.path.exists(self._voice_file):
            return False
        return True

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        if not await self.health_check():
            raise ProviderError(
                f"Piper TTS unavailable: voice={self._voice_file!r}. "
                f"Install piper-tts and a voice file, or choose another TTS.")
        try:
            result = subprocess.run(
                ["piper", "--output-raw", "--pitch-scale", "0.95",
                 "--text", request.text, self._voice_file],
                capture_output=True, timeout=120,
            )
            if result.returncode != 0:
                raise ProviderError(
                    f"Piper synthesis failed: {result.stderr.decode()[:200]}")
            audio = result.stdout
            duration_ms = int(len(audio) / (self.sample_rate * 2) * 1000)
        except Exception as error:
            raise ProviderError(f"Piper synthesis failed: {error}")

        return SynthesisResult(
            audio=audio,
            synthesis_attempt=request.synthesis_attempt or
                f"att-{hashlib.sha256(request.text.encode()).hexdigest()[:12]}",
            duration_ms=duration_ms,
            sample_rate=self.sample_rate,
            channels=1,
            sample_format="pcm_s16le",
            provider_id=self.provider_id,
        )


@dataclass
class EspeakTTSProvider(TTSProvider):
    """Ultra-light TTS: eSpeak-ng / espeak.

    Mechanical, fast, tiny. It is *not* a neural voice and should never be
    presented as one. It is the option the app reaches for when the user's
    machine is low on RAM and no neural TTS is installed.
    """

    provider_id: str = "espeak"

    def __init__(
        self,
        language: str = "en",
        speed: float = 1.0,
        *,
        sample_rate: int = 24000,
    ):
        self.sample_rate = sample_rate
        self.speed = speed
        self.language = language
        self._capabilities = ProviderCapabilities(
            streaming=False,
            notes="eSpeak-ng ultra-light TTS; mechanical, not neural",
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def health_check(self) -> bool:
        return shutil_which("espeak-ng") is not None or shutil_which("espeak") is not None

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        if not await self.health_check():
            raise ProviderError(
                "eSpeak TTS unavailable: espeak/espeak-ng not found. "
                "Install espeak-ng or choose another TTS.")
        try:
            engine = shutil_which("espeak-ng") or shutil_which("espeak")
            cmd = [
                engine, "-w", "-", "-a", "200", "-s",
                str(int(150 + self.speed * 50)),
                "-ven-us",
                request.text,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode != 0:
                raise ProviderError(
                    f"eSpeak synthesis failed: {result.stderr.decode()[:200]}")
            audio = result.stdout
            duration_ms = int(len(audio) / (self.sample_rate * 2) * 1000)
        except Exception as error:
            raise ProviderError(f"eSpeak synthesis failed: {error}")

        return SynthesisResult(
            audio=audio,
            synthesis_attempt=request.synthesis_attempt or
                f"att-{hashlib.sha256(request.text.encode()).hexdigest()[:12]}",
            duration_ms=duration_ms,
            sample_rate=self.sample_rate,
            channels=1,
            sample_format="pcm_s16le",
            provider_id=self.provider_id,
        )


def shutil_which(binary: str) -> Optional[str]:
    import shutil
    return shutil.which(binary)
