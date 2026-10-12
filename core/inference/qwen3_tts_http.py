"""HTTP-backed Qwen3-TTS provider.

A `TTSProvider` that talks to a persistent Qwen3-TTS server (see
`tts_server.py`) instead of shelling out per call. The model is loaded once by
the server and reused, so synthesis pays only inference cost — the fix for the
per-call reload that made continuous broadcasting too slow.

Falls back cleanly: if the server is unreachable, `health_check` returns False
and the caller degrades to `Qwen3TTSProvider` (subprocess) or silence. Never
fakes audio.
"""

import asyncio
import hashlib
import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from ..broadcast.providers import (
    ProviderCapabilities, ProviderUnavailable, SynthesisRequest,
    SynthesisResult, TTSProvider,
)
from .qwen3_tts import _wav_to_pcm_s16le


class Qwen3TTSHttpProvider:
    """A real `TTSProvider` backed by a persistent Qwen3-TTS HTTP server."""

    def __init__(self, base_url: str = "http://127.0.0.1:7861",
                 provider_id: str = "qwen3-tts-http",
                 timeout_seconds: float = 120.0):
        if not base_url.startswith(("http://127.0.0.1", "http://localhost")):
            raise ValueError(
                f"refusing non-local TTS endpoint {base_url!r}; localhost-only")
        self.base_url = base_url.rstrip("/")
        self.provider_id = provider_id
        self.timeout_seconds = timeout_seconds
        self._capabilities = ProviderCapabilities(
            streaming=False, max_context_tokens=1_000_000,
            notes="Qwen3-TTS via persistent local HTTP server; 24kHz mono")

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def health_check_sync(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/health")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            return bool(data.get("ok"))
        except Exception:
            return False

    async def health_check(self) -> bool:
        return await asyncio.get_event_loop().run_in_executor(
            None, self.health_check_sync)

    def _synthesize_blocking(self, text: str, ref_audio, ref_text) -> bytes:
        payload: Dict[str, Any] = {"text": text}
        if ref_audio:
            payload["ref_audio"] = ref_audio
            if ref_text:
                payload["ref_text"] = ref_text
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base_url}/synthesize", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return resp.read()
        except urllib.error.URLError as error:
            raise ProviderUnavailable(
                f"qwen3-tts server unreachable at {self.base_url}: {error}")

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        loop = asyncio.get_event_loop()
        ref_audio = None
        ref_text = None
        if request.voice_profile is not None:
            ref_audio = getattr(request.voice_profile, "reference_audio", None)
            ref_text = getattr(request.voice_profile, "reference_text", None)

        wav_bytes = await loop.run_in_executor(
            None, self._synthesize_blocking,
            request.text, ref_audio, ref_text)
        pcm = _wav_to_pcm_s16le(wav_bytes)

        # Derive duration from the PCM length at 24kHz mono s16le.
        duration_ms = int(len(pcm) / (24000 * 2) * 1000)
        attempt = request.synthesis_attempt or (
            "att-" + hashlib.sha256(request.text.encode()).hexdigest()[:12])
        return SynthesisResult(
            audio=pcm,
            synthesis_attempt=attempt,
            duration_ms=duration_ms,
            sample_rate=24000,
            channels=1,
            sample_format="pcm_s16le",
            provider_id=self.provider_id,
        )
