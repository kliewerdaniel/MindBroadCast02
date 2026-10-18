"""Lightweight LLM backend: llama.cpp / llama-cpp-python GGUF via an
OpenAI-compatible HTTP server.

This is the "less resource intensive" path the user asked for. A GGUF model is
loaded from disk, served through llama.cpp's local `llama-server` (OpenAI
compatible, port 8080), and the app talks to it with the same
OllamaCompatibleProvider protocol the Ollama backend uses. This lets a user
swap in a quantized 4-bit model that fits in 8GB RAM instead of the 18GB
qwen3:30b-a3b.

The desktop app auto-detects how many GGUF models are installed and which port
they are listening on, then lists them in the console so the user can pick.

Design
------
- The lightweight backend is a *provider*, not a hack. It implements the same
  LanguageModelProvider protocol. One LLM = one provider. The embedding and
  TTS backends are independent, so swapping the LLM never touches TTS.
- Work is bounded: ~2-4GB RAM for a Q4_K_M GGUF, ~1GB for Q3_K_M, on CPU only.
  Quantization is a user choice; the app only requires that if it claims the
  backend, a model is present at the configured path.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from ...broadcast.providers import (
    GenerationResult,
    LanguageModelProvider,
    ProviderCapabilities,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
)


@dataclass
class LlamaCppContext:
    """Everything needed to stand up a llama.cpp server."""

    model_path: str
    port: int = 8080
    ctx_size: int = 8192
    threads: int = 4
    n_gpu_layers: int = 0
    host: str = "127.0.0.1"
    server_proc: subprocess.Popen | None = None
    log: Any | None = None

    def health(self) -> bool:
        """Return True if the server answers /v1/chat/completions."""
        try:
            req = urllib.request.Request(
                f"http://{self.host}:{self.port}/v1/chat/completions",
                data=json.dumps({"messages": [{"role": "user", "content": "x"}]},
                                ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def server_args(self) -> list[str]:
        return [
            "llama-server",
            "-m", self.model_path,
            "--host", self.host,
            "--port", str(self.port),
            "--ctx-size", str(self.ctx_size),
            "--threads", str(self.threads),
            "--n-gpu-layers", str(self.n_gpu_layers),
        ]


class LlamaCppProvider(LanguageModelProvider):
    """A real local LLM backend served by llama.cpp.

    Constructed from a *model context*: a GGUF path plus a port, and the
    desktop app decides where to start it (it detects installed GGUF models and
    opens a server on the first free port).

    The only safety requirement this provider imposes is that the model file
    exists before it claims to work; the auto-detector presents only models it
    can find. A misconfigured path is reported, not silently skipped.
    """

    PROVIDER_ID = "llama-cpp"

    def __init__(
        self,
        model_path: str,
        port: int = 8080,
        ctx_size: int = 8192,
        threads: int = 4,
        n_gpu_layers: int = 0,
        *,
        context: LlamaCppContext | None = None,
        base_url: str = "http://localhost:8080",
        timeout_seconds: float = 180.0,
    ):
        if not os.path.isfile(model_path):
            raise ProviderError(
                f"llama-cpp model not found: {model_path!r}. Use the desktop app's "
                f"auto-detector to pick an installed GGUF, or point this at a path "
                f"you have downloaded.")
        self.model_path = os.path.abspath(model_path)
        self.port = port
        self.ctx_size = ctx_size
        self.threads = threads
        self.n_gpu_layers = n_gpu_layers
        self.host = "127.0.0.1"
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.context = context
        self._server: subprocess.Popen | None = None
        self._started_at: float | None = None

        self._capabilities = ProviderCapabilities(
            streaming=False,
            structured_output=False,
            max_context_tokens=ctx_size,
            supported_output_schemas=["broadcast_script_v1"],
            notes=f"llama.cpp GGUF Q4_K_M on CPU, {ctx_size} ctx",
        )

    # -- lifecycle --------------------------------------------------------

    def health_check(self) -> bool:
        return self.context.health() if self.context else self._port_open()

    def _port_open(self, retries: int = 40) -> bool:
        for _ in range(retries):
            try:
                with socket.create_connection(
                        (self.host, self.port), timeout=2):
                    return True
            except OSError:
                time.sleep(0.5)
        return False

    async def start(self) -> LlamaCppProvider:
        """Start the server if not already up and return self.

        Idempotent: if health is true, does nothing. The app owns which model
        to load; this provider just serves it.
        """
        if self.health():
            return self
        args = [
            "llama-server",
            "-m", self.model_path,
            "--host", self.host,
            "--port", str(self.port),
            "--ctx-size", str(self.ctx_size),
            "--threads", str(self.threads),
            "--n-gpu-layers", str(self.n_gpu_layers),
        ]
        # Model with more than ~6GB RAM benefits from --mmap to keep page cache
        # healthy; llama-server defaults this anyway.
        self._server = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._started_at = time.monotonic()
        # Wait for the port.
        while not self._port_open():
            if self._server.poll() is not None:
                raise ProviderUnavailable(
                    "llama-server exited early. Check that the GGUF model is "
                    "a valid quantized file and you have enough RAM.")
            time.sleep(0.5)
        return self

    async def stop(self):
        if self._server and self._server.poll() is None:
            self._server.terminate()
            try:
                self._server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._server.kill()

    # ---- protocol -------------------------------------------------------

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def generate(self, request: dict[str, Any]) -> GenerationResult:
        prompt = request.get("prompt") or request.get("objective", "")
        if not prompt:
            raise ProviderError("empty prompt")
        payload = {
            "model": self.model_path.split("/")[-1],  # informational label
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024,
            "temperature": 0.3,
        }
        data = await asyncio.get_event_loop().run_in_executor(
            None, self._post, "/v1/chat/completions", payload)
        text = ""
        for choice in data.get("choices", []):
            text += (choice.get("message", {}).get("content") or "")
        return GenerationResult(
            text=text.strip() or prompt,
            model_id=self.model_path.split("/")[-1],
            provider_id=self.PROVIDER_ID,
            latency_ms=0,
            finish_reason="stop",
        )

    async def generate_structured(self, request: dict[str, Any],
                                   schema: dict[str, Any]) -> GenerationResult:
        prompt = request.get("prompt") or request.get("objective", "")
        payload = {
            "model": self.model_path.split("/")[-1],
            "messages": [
                {"role": "system",
                 "content": schema.get("system", "Return JSON.")},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 1024,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        data = await asyncio.get_event_loop().run_in_executor(
            None, self._post, "/v1/chat/completions", payload)
        text = ""
        for choice in data.get("choices", []):
            text += (choice.get("message", {}).get("content") or "")
        return GenerationResult(
            text=text.strip(),
            model_id=self.model_path.split("/")[-1],
            provider_id=self.PROVIDER_ID,
            latency_ms=0,
            finish_reason="stop",
        )

    def _post(self, path: str, payload: dict[str, Any],
              retries: int = 3) -> dict[str, Any]:
        body = json.dumps(payload).encode()
        last_error: Exception | None = None
        for attempt in range(retries):
            req = urllib.request.Request(
                f"{self.base_url}{path}", data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as error:
                last_error = ProviderUnavailable(
                    f"llama server error {error.code}: {error.read().decode()[:120]}")
            except urllib.error.URLError as error:
                last_error = ProviderUnavailable(f"llama unreachable: {error}")
            except TimeoutError:
                last_error = ProviderTimeout("request timed out")
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
        raise last_error or ProviderUnavailable("llama request failed")
