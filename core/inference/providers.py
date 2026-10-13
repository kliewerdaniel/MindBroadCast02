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


def _looks_like_json(text: str) -> bool:
    """Whether text carries an extractable JSON object/array.

    Used to decide if a reasoning field is worth treating as the answer. The
    reasoning trace is prose; only a real JSON payload should stand in for the
    response.
    """
    return _extract_json(text) is not None


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


def _coerce_to_schema(structured: Any, schema: Dict[str, Any]) -> Any:
    """Adapt the model's actual output shape to the requested schema.

    Real models do not always honour the requested keys. qwen3:4b, asked for a
    broadcast script, reliably returns {"sentence": "..."} (after reasoning in
    the thinking field) rather than {"turns": [...]}. The orchestrator expects
    `turns`. Rather than treat every shape mismatch as a semantic failure — the
    stub never had to cope with this — coerce the common shapes:

    - {"turns": [...]}            -> unchanged
    - {"sentence"|"text"|"line": str} -> wrapped as a single turn
    - a bare string               -> wrapped as a single turn
    - a list of strings           -> wrapped as turns

    This is tolerance for the *container*, not for content: the text still
    passes through the full §15.4 validation, so an unsupported factual claim
    is refused regardless of how it was shaped.
    """
    if structured is None:
        return None
    if isinstance(structured, dict) and "turns" in structured:
        return structured
    if isinstance(structured, str):
        return {"turns": [{"sequence": 1, "text": structured,
                           "speech_act": "factual_report"}]}
    if isinstance(structured, dict):
        for key in ("sentence", "text", "line", "statement", "script"):
            value = structured.get(key)
            if isinstance(value, str) and value.strip():
                return {"turns": [{"sequence": 1, "text": value,
                                   "speech_act": "factual_report"}]}
        # Degenerate shape qwen3 emits under format:json when it treats the
        # sentence as a key: {"<the sentence>": 1}. A single string key whose
        # value is a small int is the spoken line, not a field name.
        if len(structured) == 1:
            (only_key, only_value), = structured.items()
            if (isinstance(only_key, str) and only_key.strip()
                    and isinstance(only_value, (int, float))):
                return {"turns": [{"sequence": 1, "text": only_key,
                                   "speech_act": "factual_report"}]}
        return None
    if isinstance(structured, list):
        turns = []
        for index, item in enumerate(structured, start=1):
            if isinstance(item, str):
                turns.append({"sequence": index, "text": item,
                              "speech_act": "factual_report"})
            elif isinstance(item, dict) and item.get("text"):
                turns.append({"sequence": item.get("sequence", index),
                              "text": item["text"],
                              "speech_act": item.get(
                                  "speech_act", "factual_report")})
        return {"turns": turns} if turns else None
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

    def _post(self, path: str, payload: Dict[str, Any],
              _retries: int = 5) -> Dict[str, Any]:
        """POST with bounded retry on transient server errors (§14.6).

        qwen3:4b on CPU intermittently returns HTTP 500 "token repeat limit
        reached" — a transient abort, not a malformed request. Retrying the
        identical request usually succeeds, so a 500 is retried with backoff
        rather than burned as a hard failure. A 4xx is a real client error and
        is not retried.
        """
        import time as _time
        body = json.dumps(payload).encode()
        last_error: Optional[Exception] = None
        for attempt in range(_retries):
            req = urllib.request.Request(
                f"{self.base_url}{path}", data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as error:
                # 5xx = transient server abort (e.g. token repeat limit);
                # 4xx = the request itself is wrong; do not retry those.
                if 400 <= error.code < 500:
                    raise ProviderUnavailable(
                        f"ollama rejected request ({error.code}): "
                        f"{error.read().decode()[:200]}")
                last_error = ProviderUnavailable(
                    f"ollama server error {error.code} (attempt "
                    f"{attempt + 1}/{_retries})")
            except urllib.error.URLError as error:
                last_error = ProviderUnavailable(
                    f"ollama unreachable at {self.base_url}: {error}")
            except TimeoutError:
                last_error = ProviderTimeout("ollama request timed out")
            if attempt < _retries - 1:
                _time.sleep(0.5 * (attempt + 1))
        raise last_error or ProviderUnavailable("ollama request failed")

    async def generate(self, request: Dict[str, Any]) -> GenerationResult:
        self.attempt_count += 1
        started = time.monotonic()
        objective = request.get("objective", "")
        prompt = request.get("prompt") or objective
        if not prompt:
            raise ProviderError("empty prompt")

        # num_predict must cover BOTH the reasoning trace and the answer:
        # qwen3:4b on a multi-turn bulletin spends ~8k tokens thinking before
        # it emits, so a 1024 cap hits done_reason=length with an empty
        # response and a swallowed answer. 8192 leaves room for thought + JSON.
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 8192,
                        "think": False},
        }
        data = await asyncio.get_event_loop().run_in_executor(
            None, self._post, "/api/generate", payload)

        # qwen3: answer is normally in `response`, but reasoning models may
        # return it via `thinking`. Only fall back to `thinking` when it
        # actually carries a usable JSON object — the reasoning trace itself
        # is prose, and passing 8k chars of it to the validator as if it were
        # a script is worse than reporting an honest generation failure.
        text = data.get("response", "") or ""
        if not text:
            candidate = (data.get("thinking", "")
                         or data.get("reasoning_content", "") or "")
            if _looks_like_json(candidate):
                text = candidate

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
        started = time.monotonic()
        prompt = request.get("prompt") or request.get("objective", "")
        # num_predict must cover the reasoning trace AND the answer: qwen3:4b
        # spends ~8k tokens thinking before it emits a multi-turn bulletin, so
        # a 1024 cap returns an empty response (done_reason=length). This is
        # the path the orchestrator actually calls, so the budget must live
        # here, not only in generate().
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2, "num_predict": 8192,
                        "think": False},
        }
        data = await asyncio.get_event_loop().run_in_executor(
            None, self._post, "/api/generate", payload)

        text = data.get("response", "") or ""
        if not text and _looks_like_json(
                data.get("thinking", "") or data.get("reasoning_content", "")):
            text = data.get("thinking", "") or data.get("reasoning_content", "")
        structured = _extract_json(text)
        structured = _coerce_to_schema(structured, schema)
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
