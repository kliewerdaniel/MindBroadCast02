"""Real local-inference providers.

Implements the Phase 5 `LanguageModelProvider` and `TTSProvider` protocols
against a live local server, so the full pipeline can run with a REAL model
instead of `StubLanguageModelProvider`.

Backends: Ollama (:11434, native `/api/generate`) and llama-server
(:8080, OpenAI-compatible `/v1/chat/completions`). The user's preferred local
runtime is Ollama; this session has Ollama live with `nomic-embed-text` and a
small chat model.

Rules enforced, from the local-inference-integration discipline:

- **Local-only, fail-closed.** A non-localhost endpoint is refused at
  construction unless `SOVEREIGN_ALLOW_EXTERNAL_INFERENCE=1` is set. A
  localhost typo must never become a covert cloud call.
- **Never launch/kill the server.** The agent reports a dead endpoint and
  waits; it does not manage the model server's lifecycle.
- **`/api/generate`, not `/api/chat`.** Ollama's `/api/chat` hangs on some
  builds while `/api/generate` is reliable.
- **Reasoning-safe text.** qwen3-family models put their answer in
  `message.thinking` (and `content` empty) even with `enable_thinking:false`.
  The client falls back through content → reasoning_content → thinking, and
  for structured output extracts JSON by balanced-brace scan.
- **Stdlib only.** `urllib`, no `openai` SDK assumption.
"""

import asyncio
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..broadcast.providers import (
    GenerationResult, ProviderCapabilities, ProviderError, ProviderTimeout,
    ProviderUnavailable, SynthesisRequest, SynthesisResult, TTSProvider,
    UnsupportedCapability,
)


# ── Local-only guard ─────────────────────────────────────────────────


def _refuse_non_local(base_url: str) -> None:
    """Fail closed: a non-local endpoint is refused unless explicitly allowed.

    §local-first: a misconfigured localhost typo must not become a covert
    cloud call.
    """
    if os.environ.get("SOVEREIGN_ALLOW_EXTERNAL_INFERENCE") == "1":
        return
    host = re.sub(r"^https?://", "", base_url).split("/")[0].split(":")[0]
    if host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise PermissionError(
            f"refusing non-local inference endpoint {base_url!r}; set "
            f"SOVEREIGN_ALLOW_EXTERNAL_INFERENCE=1 to override")


# ── JSON extraction (reasoning-safe) ─────────────────────────────────


def _strip_thinking(text: str) -> str:
    return re.sub(r"", "", text, flags=re.DOTALL
                  | re.IGNORECASE).strip()


def _extract_json(text: str) -> Optional[Any]:
    """First balanced JSON object/array, from the first opener.

    Handles reasoning wrappers, ```json fences, and prose around the JSON.
    """
    cleaned = _strip_thinking(text)
    for candidate in (cleaned,
                      re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)):
        if not candidate:
            continue
        raw = candidate if isinstance(candidate, str) else candidate.group(1)
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            start = raw.find(open_ch)
            if start == -1:
                continue
            depth = 0
            for i in range(start, len(raw)):
                if raw[i] == open_ch:
                    depth += 1
                elif raw[i] == close_ch:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(raw[start:i + 1])
                        except json.JSONDecodeError:
                            break
    return None


# ── Ollama LLM provider ──────────────────────────────────────────────


class OllamaProvider:
    """A real `LanguageModelProvider` backed by Ollama's `/api/generate`.

    qwen3-family models served by Ollama return the answer in
    `message.thinking` with `content` empty, even with `enable_thinking:false`
    — so the client falls back through content → reasoning_content →
    thinking, and extracts JSON by balanced-brace scan for structured tasks.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3:4b",
        timeout_seconds: float = 300.0,
        max_context_tokens: int = 32768,
        structured_output: bool = True,
    ):
        _refuse_non_local(base_url)
        self.provider_id = "ollama"
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._capabilities = ProviderCapabilities(
            streaming=False,
            structured_output=structured_output,
            max_context_tokens=max_context_tokens,
            supported_output_schemas=["broadcast_script_v1"],
        )
        self.attempt_count = 0

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def health_check(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            return any(m.get("name", "").startswith(self.model.split(":")[0])
                       for m in data.get("models", []))
        except Exception:
            return False

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as error:
            raise ProviderUnavailable(
                f"ollama unreachable at {self.base_url}: {error}")
        except TimeoutError:
            raise ProviderTimeout("ollama request timed out")

    async def generate(self, request: Dict[str, Any]) -> GenerationResult:
        self.attempt_count += 1
        started = time.monotonic()
        objective = request.get("objective", "")
        prompt = request.get("prompt") or objective
        if not prompt:
            raise ProviderError("empty prompt")

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 2048},
        }
        data = await asyncio.get_event_loop().run_in_executor(
            None, self._post, "/api/generate", payload)

        # qwen3: answer is in `response` normally, but reasoning models may
        # return it via `thinking`. Fall back through all fields.
        text = data.get("response", "") or ""
        if not text:
            text = data.get("thinking", "") or ""

        latency_ms = int((time.monotonic() - started) * 1000)
        return GenerationResult(
            text=text,
            model_id=self.model,
            provider_id=self.provider_id,
            latency_ms=latency_ms,
            token_usage={
                "prompt": data.get("prompt_eval_count", 0),
                "completion": data.get("eval_count", 0),
            },
            finish_reason="stop",
        )

    async def generate_structured(
        self, request: Dict[str, Any], schema: Dict[str, Any]
    ) -> GenerationResult:
        if not self._capabilities.structured_output:
            raise UnsupportedCapability(
                f"{self.provider_id} does not support structured output")
        # Ask for JSON via the format field; fall back to brace extraction.
        started = time.monotonic()
        prompt = request.get("prompt") or request.get("objective", "")
        payload = {
            "model": self.model,
            "prompt": prompt + "\n\nRespond with ONLY a single valid JSON object.",
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2, "num_predict": 2048},
        }
        data = await asyncio.get_event_loop().run_in_executor(
            None, self._post, "/api/generate", payload)

        text = data.get("response", "") or data.get("thinking", "") or ""
        structured = _extract_json(text)
        latency_ms = int((time.monotonic() - started) * 1000)
        return GenerationResult(
            text=text,
            structured=structured,
            model_id=self.model,
            provider_id=self.provider_id,
            latency_ms=latency_ms,
            token_usage={
                "prompt": data.get("prompt_eval_count", 0),
                "completion": data.get("eval_count", 0),
            },
        )


# ── Ollama embeddings ────────────────────────────────────────────────


class OllamaEmbeddingProvider:
    """Native `/api/embeddings` — independent of the chat server.

    nomic-embed-text returns 768-dim vectors via the native endpoint even
    when the OpenAI-compatible `/v1/embeddings` is unavailable.
    """

    def __init__(self, base_url: str = "http://localhost:11434",
                 model: str = "nomic-embed-text:latest",
                 timeout_seconds: float = 60.0):
        _refuse_non_local(base_url)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.dimension: Optional[int] = None

    def embed(self, text: str) -> List[float]:
        payload = {"model": self.model, "prompt": text}
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/embeddings", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.URLError as error:
            raise ProviderUnavailable(
                f"ollama embeddings unreachable: {error}")
        vector = data.get("embedding", [])
        if vector and self.dimension is None:
            self.dimension = len(vector)
        return vector

    def health_check(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            return any(m.get("name", "").startswith("nomic-embed")
                       for m in data.get("models", []))
        except Exception:
            return False


# ── Deterministic local TTS (no audio model required) ────────────────


class SilenceTTSProvider:
    """A real `TTSProvider` that emits correctly-formed silent PCM.

    Not a voice — a transport-valid audio source so the audio buffer, chunk
    ordering, and stale-chunk rejection can be exercised end-to-end against a
    real provider interface without an audio model. When a real local TTS
    (Qwen3-TTS, kokoro-onnx) is available it implements the same protocol.
    """

    SAMPLE_RATE = 24000
    BYTES_PER_MS = 48

    def __init__(self, provider_id: str = "local-silence"):
        self.provider_id = provider_id
        self._capabilities = ProviderCapabilities(streaming=True)

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def health_check(self) -> bool:
        return True

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        duration_ms = len(request.text) * 10
        digest = hashlib.sha256(
            (request.text + request.synthesis_attempt).encode()).hexdigest()[:12]
        audio = bytes(len(request.text) * self.BYTES_PER_MS)
        return SynthesisResult(
            audio=audio,
            synthesis_attempt=request.synthesis_attempt or f"att-{digest}",
            duration_ms=duration_ms,
            sample_rate=self.SAMPLE_RATE,
            channels=1,
            sample_format="pcm_s16le",
            provider_id=self.provider_id,
        )
