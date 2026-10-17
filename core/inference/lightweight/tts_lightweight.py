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
            # --stdout emits WAV to stdout. `-w -` writes to a *file* named
            # "-" and yields 0 bytes, which silently produced empty audio.
            cmd = [
                engine, "--stdout", "-a", "200", "-s",
                str(int(150 + self.speed * 50)),
                "-ven-us",
                request.text,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode != 0:
                raise ProviderError(
                    f"eSpeak synthesis failed: {result.stderr.decode()[:200]}")
            wav = result.stdout
            # espeak emits a WAV container; the pipeline wants raw PCM.
            audio = _wav_to_pcm(wav)
            if not audio:
                raise ProviderError("eSpeak produced no audio")
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


@dataclass
class SayTTSProvider(TTSProvider):
    """macOS built-in `say`: a real neural voice present on every Mac.

    Not a placeholder and not a download. `say` ships with macOS, speaks with
    the system voices, and can write a WAV file, so a Mac with no other TTS
    stack still has a genuine voice with zero setup.
    """

    provider_id: str = "say"

    def __init__(
        self,
        voice: Optional[str] = None,
        speed: float = 1.0,
        *,
        sample_rate: int = 24000,
    ):
        self.sample_rate = sample_rate
        self.speed = speed
        self.voice = voice
        self._capabilities = ProviderCapabilities(
            streaming=False,
            notes="macOS `say` system voice",
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def health_check(self) -> bool:
        import sys
        return sys.platform == "darwin" and shutil_which("say") is not None

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        if not await self.health_check():
            raise ProviderError(
                "macOS `say` unavailable: this backend is macOS-only.")
        import tempfile
        import os
        # `say` writes a WAV file; -r is words per minute (default ~175).
        rate = int(175 * max(0.5, min(2.0, self.speed)))
        path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
                path = fh.name
            cmd = ["say", "-o", path,
                   f"--data-format=LEI16@{self.sample_rate}", "-r", str(rate)]
            if self.voice:
                cmd += ["-v", self.voice]
            cmd.append(request.text)
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                raise ProviderError(
                    f"say synthesis failed: {result.stderr.decode()[:200]}")
            with open(path, "rb") as fh:
                audio = _wav_to_pcm(fh.read())
            if not audio:
                raise ProviderError("say produced no audio")
            duration_ms = int(len(audio) / (self.sample_rate * 2) * 1000)
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError(f"say synthesis failed: {error}")
        finally:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass

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


def _wav_to_pcm(data: bytes) -> bytes:
    """Strip a RIFF/WAVE header, returning raw PCM.

    espeak writes a WAV container; the audio buffer and the director want raw
    PCM. Falls back to returning the input unchanged when no RIFF header is
    present (some builds can emit bare PCM).
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        body = pos + 8
        if chunk_id == b"data":
            return data[body:body + size]
        pos = body + size + (size & 1)   # chunks are word-aligned
    return b""
