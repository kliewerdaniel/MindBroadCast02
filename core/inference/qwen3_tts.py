"""Real Qwen3-TTS provider via mlx-audio.

Implements the Phase 5 `TTSProvider` protocol with actual speech, replacing
`SilenceTTSProvider` (silent PCM) for the live broadcast.

Backend: Qwen3-TTS-12Hz-1.7B (MLX 4-bit) through `mlx_audio` 0.4.3, which
requires Python 3.10+. The project itself runs on Python 3.9, where the older
mlx_audio 0.2.9 lacks the `qwen3_tts` module. So the provider shells out to a
configured 3.10+ interpreter that HAS a working mlx_audio + Metal.

This matches how TTS is actually used on this machine: the CHRIS game proxies
to a "vox" Qwen3-TTS Web UI on :7860, and the in-process equivalent is this
MLX path. The provider keeps the same protocol either way.

Discipline, from the local-inference-integration rules:

- **Never launches/kills the model server or interpreter lifecycle** beyond the
  one synthesis subprocess; the model files are already cached.
- **Strips PYTHONPATH and PYTHONHOME** before the subprocess. The Hermes/agent
  shell can export a 3.9 PYTHONHOME that makes the 3.14 interpreter import the
  wrong-arch numpy/torch and crash mid-synthesis. Stripping both is the fix.
- **Fail-closed**: if the interpreter or model is unavailable, `health_check`
  returns False and the caller degrades — it never fabricates audio.
- **Model stays loaded** is not possible across subprocess calls (each is a
  fresh interpreter), so each synthesize() is one subprocess. For continuous
  broadcasting this is the cost; a persistent vox-style server is the upgrade
  path (behind the same protocol).
"""

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..broadcast.providers import (
    ProviderCapabilities, SynthesisRequest, SynthesisResult, TTSProvider,
)


# The interpreter that has a working mlx_audio with the qwen3_tts module.
# Overridable via env; auto-detected among known-good pythons otherwise.
DEFAULT_TTS_PYTHON = os.environ.get("MBS_TTS_PYTHON", "")
_MODEL = os.environ.get(
    "MBS_TTS_MODEL", "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit")
_SAMPLE_RATE = 24000


# The synthesis worker, run in a clean subprocess. Written to a temp file and
# invoked by the interpreter, so no PYTHONPATH/PYTHONHOME leaks in.
_WORKER = r'''
import sys, json, base64, warnings
warnings.filterwarnings("ignore")
import numpy as np
import soundfile as sf
from mlx_audio.tts.utils import load_model

def main():
    payload = json.loads(sys.argv[1])
    text = payload["text"]
    speed = payload.get("speed", 1.0)
    ref_audio = payload.get("ref_audio")
    ref_text = payload.get("ref_text")
    model = load_model(payload["model"])
    kwargs = {"text": text, "speed": speed, "verbose": False}
    if ref_audio:
        kwargs["ref_audio"] = ref_audio
        if ref_text:
            kwargs["ref_text"] = ref_text
    parts, sr = [], 24000
    for r in model.generate(**kwargs):
        parts.append(np.asarray(r.audio, dtype=np.float32))
        sr = getattr(r, "sample_rate", sr)
    audio = parts[0] if len(parts) == 1 else np.concatenate(parts)
    out = payload["out"]
    sf.write(out, audio, sr)
    print(json.dumps({
        "ok": True, "path": out, "sample_rate": int(sr),
        "duration_ms": int(len(audio) / sr * 1000),
    }))

main()
'''


def _detect_interpreter() -> Optional[str]:
    """Find a python with a working mlx_audio qwen3_tts module."""
    candidates = [DEFAULT_TTS_PYTHON] if DEFAULT_TTS_PYTHON else []
    candidates += [
        "/opt/homebrew/bin/python3.14",
        "/opt/homebrew/bin/python3.13",
        "/opt/homebrew/bin/python3.12",
        "/opt/homebrew/bin/python3.11",
        sys.executable,
    ]
    for candidate in candidates:
        if not candidate or not os.path.exists(candidate):
            continue
        try:
            result = subprocess.run(
                [candidate, "-c",
                 "import mlx_audio.tts.models.qwen3_tts, soundfile"],
                capture_output=True, timeout=30,
                env=_clean_env())
            if result.returncode == 0:
                return candidate
        except Exception:
            continue
    return None


def _clean_env() -> Dict[str, str]:
    """Strip PYTHONPATH and PYTHONHOME — the env-leak that crashes a
    different-Python subprocess mid-synthesis."""
    return {k: v for k, v in os.environ.items()
            if k not in ("PYTHONPATH", "PYTHONHOME")}


class Qwen3TTSProvider:
    """A real `TTSProvider` backed by Qwen3-TTS (MLX) via a subprocess.

    Emits 24 kHz mono float WAV, converted to the internal pcm_s16le the
    audio buffer expects. Supports optional zero-shot voice cloning when a
    `ref_audio` (+ optional `ref_text`) is supplied.
    """

    def __init__(self, provider_id: str = "qwen3-tts-mlx",
                 model: str = _MODEL,
                 timeout_seconds: float = 180.0):
        self.provider_id = provider_id
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._capabilities = ProviderCapabilities(
            streaming=False, max_context_tokens=1_000_000,
            notes="Qwen3-TTS 12Hz MLX 4-bit; 24kHz mono; optional ICL voice clone")
        self._interpreter: Optional[str] = None
        self._interpreter_checked = False

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def interpreter(self) -> Optional[str]:
        if not self._interpreter_checked:
            self._interpreter = _detect_interpreter()
            self._interpreter_checked = True
        return self._interpreter

    def health_check_sync(self) -> bool:
        interpreter = self.interpreter()
        if interpreter is None:
            return False
        # A real one-line synthesis proves the model loads, not just imports.
        try:
            result = self._synthesize_blocking(
                "test", attempt="health", ref_audio=None, ref_text=None)
            return bool(result.get("ok"))
        except Exception:
            return False

    async def health_check(self) -> bool:
        return await asyncio.get_event_loop().run_in_executor(
            None, self.health_check_sync)

    def _synthesize_blocking(self, text: str, attempt: str,
                             ref_audio: Optional[str],
                             ref_text: Optional[str]) -> Dict[str, Any]:
        interpreter = self.interpreter()
        if interpreter is None:
            raise RuntimeError(
                "no python interpreter with a working mlx_audio qwen3_tts "
                "module was found; set MBS_TTS_PYTHON")

        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False) as worker_file:
            worker_file.write(_WORKER)
            worker_path = worker_file.name

        fd, out_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)

        payload = {
            "text": text, "model": self.model, "out": out_path,
            "ref_audio": ref_audio, "ref_text": ref_text,
        }
        try:
            result = subprocess.run(
                [interpreter, worker_path, json.dumps(payload)],
                capture_output=True, timeout=self.timeout_seconds,
                env=_clean_env())
            if result.returncode != 0:
                raise RuntimeError(
                    "qwen3-tts synthesis failed: "
                    + result.stderr.decode(errors="replace")[-400:])
            return json.loads(result.stdout.decode().strip().splitlines()[-1])
        finally:
            for path in (worker_path,):
                try:
                    os.unlink(path)
                except OSError:
                    pass

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        loop = asyncio.get_event_loop()
        ref_audio = None
        ref_text = None
        if request.voice_profile is not None:
            ref_audio = getattr(request.voice_profile, "reference_audio", None)
            ref_text = getattr(request.voice_profile, "reference_text", None)

        info = await loop.run_in_executor(
            None, self._synthesize_blocking,
            request.text, request.synthesis_attempt, ref_audio, ref_text)

        with open(info["path"], "rb") as handle:
            wav_bytes = handle.read()
        try:
            os.unlink(info["path"])
        except OSError:
            pass

        # Convert the WAV to raw pcm_s16le for the internal buffer. The buffer
        # and its format normalization (§16.5) expect canonical samples.
        pcm = _wav_to_pcm_s16le(wav_bytes)
        attempt = request.synthesis_attempt or (
            "att-" + hashlib.sha256(request.text.encode()).hexdigest()[:12])
        return SynthesisResult(
            audio=pcm,
            synthesis_attempt=attempt,
            duration_ms=info.get("duration_ms"),
            sample_rate=info.get("sample_rate", _SAMPLE_RATE),
            channels=1,
            sample_format="pcm_s16le",
            provider_id=self.provider_id,
        )


def _wav_to_pcm_s16le(wav_bytes: bytes) -> bytes:
    """Strip the WAV header, returning canonical little-endian s16 mono PCM.

    No resampling: Qwen3-TTS already emits 24 kHz mono, which is the canonical
    internal rate. A minimal RIFF parser avoids a numpy dependency in the
    project's 3.9 interpreter.
    """
    if wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        raise RuntimeError("not a RIFF/WAVE file")
    pos = 12
    data_offset = None
    data_size = 0
    bits = 16
    channels = 1
    while pos + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[pos:pos + 4]
        chunk_size = int.from_bytes(wav_bytes[pos + 4:pos + 8], "little")
        body = pos + 8
        if chunk_id == b"fmt ":
            channels = int.from_bytes(wav_bytes[body + 2:body + 4], "little")
            bits = int.from_bytes(wav_bytes[body + 14:body + 16], "little")
        elif chunk_id == b"data":
            data_offset = body
            data_size = chunk_size
            break
        pos = body + chunk_size + (chunk_size & 1)
    if data_offset is None:
        raise RuntimeError("WAV has no data chunk")
    pcm = wav_bytes[data_offset:data_offset + data_size]
    if bits == 16 and channels == 1:
        return pcm
    raise RuntimeError(
        f"unexpected WAV format: {bits}-bit {channels}-channel "
        "(expected 16-bit mono)")
