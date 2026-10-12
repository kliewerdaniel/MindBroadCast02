"""Provider abstraction for language models and text-to-speech.

Spec §14.1 and §16.2.

The load-bearing rule in both interfaces: **a provider must not claim support
for a capability that cannot reliably deliver it** (§14.1). `capabilities()`
returns what actually works, and the caller must degrade gracefully when a
needed capability is absent rather than assume it.

`StubLanguageModelProvider` and `StubTTSProvider` exist so every gate can pass
without a live model — the same discipline as Phase 2's `StubTransport`. A gate
that requires a real model is a gate that never runs in CI.
"""

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# ── §14.1 Capability declarations ────────────────────────────────────


class Capability:
    """A named capability a provider may or may not support."""
    STREAMING = "streaming"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_USE = "tool_use"
    LONG_CONTEXT = "long_context"
    VISION = "vision"


@dataclass
class ProviderCapabilities:
    """§14.1. Only what this provider reliably delivers.

    Not every provider supports streaming, structured output, tool use, or the
    same context length.
    """
    streaming: bool = False
    structured_output: bool = False
    tool_use: bool = False
    max_context_tokens: int = 4096
    supported_output_schemas: List[str] = field(default_factory=list)
    notes: str = ""

    def supports(self, capability: str) -> bool:
        return bool(getattr(self, capability, False))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "streaming": self.streaming,
            "structured_output": self.structured_output,
            "tool_use": self.tool_use,
            "max_context_tokens": self.max_context_tokens,
            "supported_output_schemas": list(self.supported_output_schemas),
        }


@dataclass
class GenerationResult:
    """What a provider returns for one attempt."""
    text: str = ""
    structured: Optional[Dict[str, Any]] = None
    model_id: str = ""
    provider_id: str = ""
    latency_ms: int = 0
    token_usage: Optional[Dict[str, int]] = None
    finish_reason: str = "stop"


class ProviderError(Exception):
    pass


class ProviderUnavailable(ProviderError):
    """The provider cannot serve this request right now."""


class ProviderTimeout(ProviderError):
    """The request exceeded its deadline."""


class UnsupportedCapability(ProviderError):
    """The requested capability is one this provider does not claim."""


@runtime_checkable
class LanguageModelProvider(Protocol):
    """§14.1. The domain must not depend on provider-specific APIs."""

    provider_id: str

    def capabilities(self) -> ProviderCapabilities: ...

    async def health_check(self) -> bool: ...

    async def generate(self, request: Dict[str, Any]) -> GenerationResult: ...

    async def generate_structured(
        self, request: Dict[str, Any], schema: Dict[str, Any]
    ) -> GenerationResult: ...


# ── §16.2 TTS ─────────────────────────────────────────────────────────


@dataclass
class SynthesisRequest:
    """§16.2."""
    segment_id: str = ""
    script_turn_id: str = ""
    text: str = ""
    voice_profile: Any = None            # VoiceProfile
    language: str = "en"
    rate: float = 1.0
    output_format: str = "pcm_s16le"
    synthesis_attempt: str = ""
    config_version: str = ""
    deadline: Optional[datetime] = None


@dataclass
class SynthesisResult:
    """§16.2. Includes a synthesis-attempt identifier.

    The attempt id is what lets the buffer reject chunks from an obsolete
    generation (§13.5).
    """
    audio: bytes = b""
    synthesis_attempt: str = ""
    duration_ms: Optional[int] = None
    sample_rate: int = 24000
    channels: int = 1
    sample_format: str = "pcm_s16le"
    provider_id: str = ""
    latency_ms: int = 0


@runtime_checkable
class TTSProvider(Protocol):
    """§16.2. Independent of the language-model provider (§16.1)."""

    provider_id: str

    def capabilities(self) -> ProviderCapabilities: ...

    async def health_check(self) -> bool: ...

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult: ...


# ── Stub implementations ─────────────────────────────────────────────


class StubLanguageModelProvider:
    """Deterministic stand-in so the gates need no live model.

    Returns a fixed script shape built from the request's objective. This is
    NOT a simulation of model behaviour — it exercises the contracts
    (idempotency, deadlines, capability declaration, structured output)
    without pretending to generate language.
    """

    def __init__(self, provider_id: str = "stub-llm",
                 streaming: bool = False,
                 structured_output: bool = True,
                 max_context_tokens: int = 8192):
        self.provider_id = provider_id
        self._capabilities = ProviderCapabilities(
            streaming=streaming,
            structured_output=structured_output,
            max_context_tokens=max_context_tokens,
            supported_output_schemas=["broadcast_script_v1"],
        )
        self.attempt_count = 0
        self.requests: List[Dict[str, Any]] = []
        self._fail_times = 0

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def health_check(self) -> bool:
        return True

    def fail_next(self, times: int = 1) -> None:
        """Test hook: simulate transient provider failure."""
        self._fail_times = times

    async def generate(self, request: Dict[str, Any]) -> GenerationResult:
        self.attempt_count += 1
        self.requests.append(request)

        if self._fail_times > 0:
            self._fail_times -= 1
            raise ProviderUnavailable("simulated transient failure")

        objective = request.get("objective", "")
        text = f"{objective}"
        if not text:
            raise ProviderError("empty objective")
        return GenerationResult(
            text=text,
            model_id="stub-model",
            provider_id=self.provider_id,
            latency_ms=1,
            token_usage={"prompt": 64, "completion": 32},
        )

    async def generate_structured(
        self, request: Dict[str, Any], schema: Dict[str, Any]
    ) -> GenerationResult:
        if not self._capabilities.structured_output:
            raise UnsupportedCapability(
                f"{self.provider_id} does not support structured output")
        result = await self.generate(request)
        objective = request.get("objective", "")
        result.structured = {
            "turns": [{
                "sequence": 1,
                "text": objective,
                "speech_act": "factual_report",
                "claim_references": request.get("claim_references", []),
                "evidence_references": request.get("evidence_references", []),
            }],
            "schema_id": schema.get("$id", "broadcast_script_v1"),
        }
        return result


class StubTTSProvider:
    """Deterministic TTS stand-in.

    Emits a silent PCM payload sized by text length. Enough to exercise
    ordering, chunk binding, cancellation, and stale-chunk rejection without
    any audio model.
    """

    # 24 kHz, 16-bit mono, ~10ms of audio per character of text.
    SAMPLE_RATE = 24000
    BYTES_PER_MS = 48          # 24000 Hz * 2 bytes / 1000 ms

    def __init__(self, provider_id: str = "stub-tts",
                 streaming: bool = True):
        self.provider_id = provider_id
        self._capabilities = ProviderCapabilities(
            streaming=streaming,
            max_context_tokens=1_000_000,
        )
        self.attempt_count = 0
        self._fail_times = 0

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def health_check(self) -> bool:
        return True

    def fail_next(self, times: int = 1) -> None:
        self._fail_times = times

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        self.attempt_count += 1
        if self._fail_times > 0:
            self._fail_times -= 1
            raise ProviderUnavailable("simulated TTS failure")

        digest = hashlib.sha256(request.text.encode()).hexdigest()[:12]
        attempt = request.synthesis_attempt or f"att-{digest}"
        duration_ms = len(request.text) * 10
        audio = bytes(len(request.text) * self.BYTES_PER_MS)
        return SynthesisResult(
            audio=audio,
            synthesis_attempt=attempt,
            duration_ms=duration_ms,
            sample_rate=self.SAMPLE_RATE,
            channels=1,
            sample_format="pcm_s16le",
            provider_id=self.provider_id,
            latency_ms=2,
        )


# ── §14.2 Model routing ──────────────────────────────────────────────


# §14.2's capability map. A capability map, not a requirement to use different
# model families per row: on constrained hardware one model handles several
# tasks sequentially. The architecture preserves the ability to route
# differently later.
TASK_CAPABILITY_MAP: Dict[str, str] = {
    "article_classification": "fast_inference",
    "entity_extraction": "structured_output",
    "claim_extraction": "instruction_following",
    "contradiction_analysis": "reasoning",
    "story_ranking_assistance": "structured_output",
    "persona_dialogue": "natural_language",
    "long_form_analysis": "long_context",
    "script_validation": "independent_verification",
    "embedding_generation": "dedicated_embedding_model",
    "script_generation": "natural_language",
}


@dataclass
class RouteDecision:
    task_type: str
    provider_id: str
    required_capability: str
    rationale: str = ""


def route_task(
    task_type: str,
    providers: Dict[str, LanguageModelProvider],
    max_context_tokens: int = 0,
) -> Optional[RouteDecision]:
    """§14.2. Select a provider that actually claims the required capability.

    Returns None when no available provider can serve the task — which is the
    correct outcome. Guessing would claim a capability the provider does not
    have, which §14.1 forbids.
    """
    required = TASK_CAPABILITY_MAP.get(task_type, "natural_language")
    for provider_id, provider in providers.items():
        caps = provider.capabilities()
        if max_context_tokens and caps.max_context_tokens < max_context_tokens:
            continue
        if required == "streaming" and not caps.streaming:
            continue
        if required == "structured_output" and not caps.structured_output:
            continue
        if required == "long_context" and caps.max_context_tokens < 32768:
            continue
        return RouteDecision(
            task_type=task_type,
            provider_id=provider_id,
            required_capability=required,
            rationale=f"{provider_id} claims {required}",
        )
    return None
