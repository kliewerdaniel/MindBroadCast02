"""Modularity: model + TTS selection for the desktop app.

MindBroadcast ships on macOS, Linux, and Windows. Deployment is meant to be
easiest for a user who may not want 18GB of Qwen3 weights: they pick lighter
models that still satisfy the gates. The architecture is deliberately
modular:

    config/                   # YAML config + the ModelContext registry
    core/inference/providers.py   # OllamaProvider, OllamaEmbeddingProvider
    core/inference/lightweight/   # llama-cpp-python backend + Piper TTS + eSpeak
    core/inference/autonomous.py  # reads config, builds providers

A provider declares what it can do via ProviderCapabilities and never claims
support it cannot deliver. The runtime and console ask what the provider
supports, and degrade to the next available option rather than crashing.

Design rules
------------
- Local-only, fail-closed. Never reach a non-local endpoint. Every backend
  refuses non-localhost on construction.
- Everything is discovered, not hard-coded. The console auto-detects available
  providers and shows them; if none are usable, it tells the user, it does not
  guess.
- Model + TTS are chosen per-run, not compiled into the binary. qwen3:30b-a3b
  is the default because it is the default, not because it is immutable.
- A "lightweight" backend is a first-class provider, not a hack. It replaces
  exactly one: one LLM language model, one embedding model, one TTS backend.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_CONFIG_FILENAME = "mindbroadcast.yaml"
_CONFIG_DEFAULT = """\
# MindBroadcast configuration.
#
# This file is optional. Every key here has a precedence order and sensible
# defaults; if the file is absent the app starts with the defaults below. The
# desktop app reads this file and presents its choices to the user on first
# launch when a setting is unconfigured.
#
# ---------------------------------------------------------------- Model ---
# The language model runs an Ollama-compatible server (Ollama, llama-cpp-python
# server, LM Studio, llama-server). Connectivity is required before the app can
# generate. If the server is down, the console tells you instead of guessing.
llm:
  # URL of the Ollama-compatible server.
  #   Ollama:            http://localhost:11434
  #   llama-cpp-python:  http://localhost:8080
  #   LM Studio:         http://localhost:1234
  #   llama-server:      http://localhost:8080
  base_url: http://localhost:11434
  # Model name as registered with the server. Use "ollama pull <name>" to add
  # models. On a fresh machine, auto-detection lists what is installed; choose
  # the best available and the app still works.
  model: qwen3:30b-a3b
  # Timeout for a single generation request, in seconds.
  timeout_seconds: 300
  # For the embedding model (used for similarity, clustering, semantic search).
  # Leave as "" to disable embeddings. The app degrades gracefully (semantic
  # similarity falls back to heuristics).
  embedding_model: nomic-embed-text:latest

# ------------------------------------------------------------------ TTS ---
# Text-to-speech. The Qwen3-TTS path requires a persistent server on :7861 and
# mlx_audio; it is the best voice but is also the biggest requirement. On a
# machine without that stack, the console auto-detects the lighter options
# below and uses them. They are real neural TTS: they are not a placeholder.
tts:
  # Preferred TTS backend. Options: "qwen3-tts-http" (persistent server),
  # "piper" (offline neural, small), "espeak" (ultra-light, system)
  # "silence" (structurally valid PCM for testing/traing, no audio model).
  # The first available option wins; the console shows all available.
  backend: qwen3-tts-http
  # Speed multiplier. 1.0 = normal.
  speed: 1.0
  # Persistent Qwen3-TTS server port. Only used by the qwen3-tts-http backend.
  qwen3_tts_server_port: 7861

# ------------------------------------------------------------- Optional ---
# Reliability and behaviour knobs (see the spec for the full story).
# Uncomment a line to change it; leave commented for the default.

# # Max segments per broadcast cycle (0 = the station runs while it has
# # something valuable to say).
# max_segments: 10
#
# # Language the station broadcasts in. The world feeds are in many languages
# # but everything here is English; set "en-US" or "en-GB".
# language: en-US
#
# # Rate multiplier for TTS (same as the global speed above, per-segment).
# rate: 1.0
"""

# The model enum is serialized by the registry's auto-detection; these are
# human-readable labels shown in the console when several models of one kind
# are installed.
MODEL_LABEL_MAP = {
    "qwen3:30b-a3b": "Qwen3 30b (longform specialist, 18GB)",
    "qwen3:4b": "Qwen3 4b (light, single-turn)",
    "gemma3:4b": "Gemma 3 4b (compact, fast)",
    "llama3.1:8b": "Llama 3.1 8b (balanced CPU)",
    "llama3.2:3b": "Llama 3.2 3b (smallest, low RAM)",
}

TTS_LABEL_MAP = {
    "qwen3-tts-http": "Qwen3-TTS (persistent server, best voice)",
    "piper": "Piper (offline neural, small)",
    "espeak": "eSpeak (ultra-light, system voice)",
    "silence": "Silence (test/traffic, no audio model)",
}


@dataclass
class ModelContext:
    """One machine-readable assembled model for the desktop app.

    The desktop app holds a *context*, not a hardcoded string: the context is
    created from the user's chosen config on first launch and shown in the
    console. Each context object owns exactly the providers it needs.
    """

    # Path this config file was read from (or None for defaults).
    config_path: Optional[Path] = None
    # LLM backend: "ollama", "llama_cpp", "lm_studio", "llama_server"
    llm_backend: str = "ollama"
    # URL of the Ollama-compatible server. Refused if non-local and the
    # fail-closed guard is active.
    llm_base_url: str = "http://localhost:11434"
    # Model name as registered with the server.
    llm_model: str = "qwen3:30b-a3b"
    # Timeout for generation.
    llm_timeout: float = 300.0
    # Embedding model (or "" to disable).
    embedding_model: str = "nomic-embed-text:latest"

    # TTS backend.
    tts_backend: str = "qwen3-tts-http"
    # TTS speed.
    tts_speed: float = 1.0
    # TTS server port (Qwen3-TTS only).
    tts_port: int = 7861

    # Derived, computed at assembly time (not part of the YAML).
    _llm_provider: Any = field(default=None, init=False)
    _tts_provider: Any = field(default=None, init=False)
    _embedding_provider: Any = field(default=None, init=False)

    # ---- construction ---------------------------------------------------

    @classmethod
    def from_path(cls, path: Optional[Path] = None) -> "ModelContext":
        """Read config from the file next to the app, falling back to defaults."""
        if path is None:
            path = Path.cwd() / _CONFIG_FILENAME
        if path.is_file():
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        else:
            data = {}
        return cls._from_mapping(data, source=path)

    @classmethod
    def from_mapping(cls, mapping: Dict[str, Any],
                     source: Optional[Path] = None) -> "ModelContext":
        return cls._from_mapping(mapping, source=source)

    @classmethod
    def _from_mapping(cls, data: Dict[str, Any],
                      source: Optional[Path]) -> "ModelContext":
        config = data.get("mindbroadcast", data)  # legacy support

        llm = config.get("llm", {}) or {}
        tts = config.get("tts", {}) or {}

        ctx = cls(
            config_path=source,
            llm_backend=str(llm.get("backend", "ollama")),
            llm_base_url=str(llm.get("base_url", "http://localhost:11434")),
            llm_model=str(llm.get("model", "qwen3:30b-a3b")),
            llm_timeout=float(llm.get("timeout_seconds", 300)),
            embedding_model=str(llm.get("embedding_model",
                                       "nomic-embed-text:latest")),
            tts_backend=str(tts.get("backend", "qwen3-tts-http")),
            tts_speed=float(tts.get("speed", 1.0)),
            tts_port=int(tts.get("qwen3_tts_server_port", 7861)),
        )
        return ctx

    # ---- accessors ------------------------------------------------------

    def llm_provider(self):
        """Build the language-model provider for this context."""
        if self._llm_provider is None:
            self._llm_provider = self._build_llm_provider()
        return self._llm_provider

    def _build_llm_provider(self):
        """Assemble the LLM provider. The desktop app chooses the backend from
        the detected set."""
        if self.llm_backend == "llama_cpp":
            from ..inference.lightweight.llama_cpp_provider import LlamaCppProvider
            # LlamaCppProvider is constructed from a GGUF *path*, not a model
            # name; it refuses a path that does not exist (fail-closed).
            return LlamaCppProvider(
                model_path=self.llm_model,
                base_url=self.llm_base_url,
                timeout_seconds=self.llm_timeout,
            )
        # ollama and the other OpenAI-compatible servers share one class: they
        # all speak /api/generate or the OpenAI chat shape on a local port.
        from ..inference.providers import OllamaProvider
        return OllamaProvider(
            base_url=self.llm_base_url,
            model=self.llm_model,
            timeout_seconds=self.llm_timeout,
        )

    def embedding_provider(self):
        """Build the embedding provider. Returns None when embeddings are
        disabled (embedding_model == "")."""
        if self.embedding_model == "":
            return None
        if self._embedding_provider is None:
            from .providers import OllamaEmbeddingProvider
            self._embedding_provider = OllamaEmbeddingProvider(
                base_url=self.llm_base_url,
                model=self.embedding_model,
            )
        return self._embedding_provider

    def tts_provider(self):
        """Build the TTS provider for this context."""
        if self._tts_provider is None:
            self._tts_provider = self._build_tts_provider()
        return self._tts_provider

    def _build_tts_provider(self):
        if self.tts_backend == "piper":
            from ..inference.lightweight.tts_lightweight import PiperTTSProvider
            return PiperTTSProvider(speed=self.tts_speed)
        if self.tts_backend == "espeak":
            from ..inference.lightweight.tts_lightweight import EspeakTTSProvider
            return EspeakTTSProvider(speed=self.tts_speed)
        if self.tts_backend == "say":
            from ..inference.lightweight.tts_lightweight import SayTTSProvider
            return SayTTSProvider(speed=self.tts_speed)
        if self.tts_backend == "silence":
            # Structurally valid silent PCM: the station runs without any audio
            # model. Real TTSProvider, honest about having no voice.
            from ..inference.providers import SilenceTTSProvider
            return SilenceTTSProvider()
        # qwen3-tts-http: the default, persistent server path.
        from ..inference.qwen3_tts_http import Qwen3TTSHttpProvider
        return Qwen3TTSHttpProvider(
            base_url=f"http://127.0.0.1:{self.tts_port}",
            provider_id="qwen3-tts-http",
        )

    # ---- serialisation --------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "config_path": str(self.config_path) if self.config_path else None,
            "llm": {
                "backend": self.llm_backend,
                "base_url": self.llm_base_url,
                "model": self.llm_model,
                "timeout_seconds": self.llm_timeout,
                "embedding_model": self.embedding_model,
            },
            "tts": {
                "backend": self.tts_backend,
                "speed": self.tts_speed,
                "qwen3_tts_server_port": self.tts_port,
            },
        }

    def save(self, path: Optional[Path] = None) -> Path:
        """Write the canonical config file."""
        path = Path(path) if path else Path.cwd() / _CONFIG_FILENAME
        mapping = {
            "mindbroadcast": {
                "llm": {
                    "backend": self.llm_backend,
                    "base_url": self.llm_base_url,
                    "model": self.llm_model,
                    "timeout_seconds": self.llm_timeout,
                    "embedding_model": self.embedding_model,
                },
                "tts": {
                    "backend": self.tts_backend,
                    "speed": self.tts_speed,
                    "qwen3_tts_server_port": self.tts_port,
                },
            },
        }
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(mapping, fh, sort_keys=False, default_flow_style=False)
        return path

    def __post_init__(self):
        # The config file may sit anywhere; the app canonicalises to
        # cwd/mindbroadcast.yaml on save and loads the nearest one there.
        pass
