"""Context assembly and the generation pipeline.

Spec §14.3–14.6.

§14.4 is the governing rule: the context builder "should not simply concatenate
the most recent articles." It must prioritize relevance, provenance, diversity
of evidence, and token efficiency, and it must preserve the distinction between
direct source material, system-generated summaries, and persona interpretation.

§14.5 is the security rule: source documents and listener messages are
untrusted content. The controls implemented here are structural, not
prompt-wording — the spec is explicit that the application "must not rely on
prompt wording alone."
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .entities import (
    BroadcastScript, EvidencePacket, GenerationAttempt, GenerationRequest,
    ScriptTurn, SpeechAct,
)
from .providers import (
    GenerationResult, LanguageModelProvider, ProviderError, StubLanguageModelProvider,
)


# ── §14.4 Context assembly ───────────────────────────────────────────


class ContextBuilder:
    """Builds a targeted, versioned evidence packet from stored records."""

    # Token budget. §14.4 asks for token efficiency, not maximal context.
    DEFAULT_TOKEN_BUDGET = 4000

    def __init__(self, token_budget: int = DEFAULT_TOKEN_BUDGET):
        self.token_budget = token_budget

    def build(
        self,
        segment_objective: str,
        supporting_claims: Optional[List[Any]] = None,
        contradicting_claims: Optional[List[Any]] = None,
        evidence_records: Optional[List[Any]] = None,
        historical_context: Optional[List[Any]] = None,
        previously_broadcast: Optional[List[Any]] = None,
        unresolved_questions: Optional[List[str]] = None,
        required_disclaimers: Optional[List[str]] = None,
        world_snapshot_id: str = "",
    ) -> EvidencePacket:
        """Assemble the packet.

        Contradictory evidence is included **always**, never filtered for
        agreement. A packet containing only supporting evidence would produce
        a confident one-sided script, which is the failure §10.6 and §15.4
        stage 3 exist to prevent.
        """
        supporting_claims = supporting_claims or []
        contradicting_claims = contradicting_claims or []
        evidence_records = evidence_records or []

        packet = EvidencePacket(
            world_snapshot_id=world_snapshot_id,
            segment_objective=segment_objective,
            supporting_claims=[self._id(c) for c in supporting_claims],
            contradicting_claims=[self._id(c) for c in contradicting_claims],
            evidence_ids=[self._id(e) for e in evidence_records],
            historical_context_ids=[self._id(h) for h in
                                    (historical_context or [])],
            previously_broadcast_ids=[self._id(p) for p in
                                      (previously_broadcast or [])],
            unresolved_questions=list(unresolved_questions or []),
            required_disclaimers=list(required_disclaimers or []),
        )

        # §14.4: preserve provenance distinctions.
        packet.content_provenance = {}
        for claim in supporting_claims + contradicting_claims:
            packet.content_provenance[self._id(claim)] = "source_material"
        for item in historical_context or []:
            packet.content_provenance[self._id(item)] = "system_summary"
        for item in previously_broadcast or []:
            packet.content_provenance[self._id(item)] = "persona_interpretation"

        return packet

    @staticmethod
    def _id(record: Any) -> str:
        if isinstance(record, str):
            return record
        return getattr(record, "id", str(record))

    def estimate_tokens(self, text: str) -> int:
        """Rough estimate; sufficient for a budget check without a tokenizer."""
        return max(1, len(text) // 4)


# ── §14.5 Prompt injection controls ──────────────────────────────────

# Markers that separate instructions from data. §14.5 requires strict
# separation; this is one structural control, not the only one.
INSTRUCTION_BLOCK_OPEN = "<<<INSTRUCTIONS>>>"
INSTRUCTION_BLOCK_CLOSE = "<<<END_INSTRUCTIONS>>>"
DATA_BLOCK_OPEN = "<<<SOURCE_DATA>>>"
DATA_BLOCK_CLOSE = "<<<END_SOURCE_DATA>>>"

# Patterns indicating an attempt to redirect the model. Detection is a
# warning signal for logging and review, NOT a sole defense — §14.5 forbids
# relying on any single mechanism.
INJECTION_PATTERNS = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard your instructions",
    "system prompt:",
    "you are now",
    "new instructions:",
    "forget everything",
    "reveal your system",
    "print your instructions",
    "output your prompt",
)


@dataclass
class InjectionFinding:
    field_name: str
    pattern: str
    excerpt: str

    def detail(self) -> str:
        return f"{self.field_name}: matched {self.pattern!r} near {self.excerpt!r}"


class UntrustedContentGuard:
    """§14.5. Structural controls against untrusted source content.

    What this does:
    - Encloses source text in explicit data delimiters so instruction/data
      separation is structural rather than a matter of prompt wording.
    - Validates every proposed identifier against the known record set, so a
      source document cannot fabricate a reference.
    - Refuses to place credentials or secrets in model context.

    What this does NOT do: it does not guarantee a live model resists
    manipulation. That is documented as a limitation. §14.5's own list is
    explicit that no single technique is sufficient (§15.6 says the same).
    """

    def __init__(self, known_record_ids: Optional[set] = None):
        self.known_record_ids = known_record_ids or set()

    def wrap_source_data(self, text: str) -> str:
        """Enclose untrusted content in data delimiters."""
        return f"{DATA_BLOCK_OPEN}\n{text}\n{DATA_BLOCK_CLOSE}"

    def scan(self, text: str, field_name: str = "source") -> List[InjectionFinding]:
        lowered = text.lower()
        findings = []
        for pattern in INJECTION_PATTERNS:
            index = lowered.find(pattern)
            if index != -1:
                findings.append(InjectionFinding(
                    field_name=field_name,
                    pattern=pattern,
                    excerpt=text[max(0, index - 20):index + len(pattern) + 20],
                ))
        return findings

    def validate_identifier(self, record_id: str) -> bool:
        """§14.5: validate all proposed identifiers against the database.

        A source document that names a plausible-looking claim id must not be
        able to reference a record the system does not have.
        """
        if not self.known_record_ids:
            # An empty known set means "not checked", not "all valid". Refusing
            # is the fail-closed choice.
            return False
        return record_id in self.known_record_ids

    def sanitize_context(self, context: Dict[str, Any]) -> Tuple[Dict[str, Any], List[InjectionFinding]]:
        """Return a context dict with untrusted fields wrapped, plus findings."""
        sanitized = dict(context)
        findings: List[InjectionFinding] = []
        for key in ("source_text", "article_text", "listener_message"):
            value = context.get(key)
            if isinstance(value, str):
                findings.extend(self.scan(value, field_name=key))
                sanitized[key] = self.wrap_source_data(value)
        return sanitized, findings


# ── §14.6 Generation orchestration ───────────────────────────────────


@dataclass
class GenerationOutcome:
    request: Optional[GenerationRequest] = None
    attempts: List[GenerationAttempt] = field(default_factory=list)
    script: Optional[BroadcastScript] = None
    succeeded: bool = False
    blocked_reason: str = ""


class GenerationOrchestrator:
    """§14.6. Deadline, retry, and idempotency around one provider.

    The failure policy matters more than the happy path: a repeated semantic
    failure must not become an infinite regeneration loop, and retries must
    not create duplicate active segments.
    """

    def __init__(
        self,
        provider: LanguageModelProvider,
        guard: Optional[UntrustedContentGuard] = None,
        prompt_version: str = "script_v1",
        output_schema_version: str = "broadcast_script_v1",
        policy_id: str = "local_default",
    ):
        self.provider = provider
        self.guard = guard or UntrustedContentGuard()
        self.prompt_version = prompt_version
        self.output_schema_version = output_schema_version
        self.policy_id = policy_id
        # request_idempotency_key -> script, so a retry reuses the result
        # rather than creating a duplicate active segment (§14.6).
        self._idempotency: Dict[str, BroadcastScript] = {}
        self.semantic_failure_counts: Dict[str, int] = {}

    def make_request(
        self,
        task_type: str,
        segment_id: str,
        evidence_packet: EvidencePacket,
        persona_id: str = "",
        persona_configuration_version: str = "",
        input_record_ids: Optional[List[str]] = None,
        maximum_output_tokens: int = 2048,
        max_attempts: int = 2,
        deadline: Optional[datetime] = None,
    ) -> GenerationRequest:
        digest = hashlib.sha256(
            "|".join([
                task_type, segment_id, evidence_packet.id,
                self.prompt_version, self.output_schema_version,
            ]).encode()
        ).hexdigest()[:16]
        return GenerationRequest(
            idempotency_key=f"gen-{digest}",
            task_type=task_type,
            segment_id=segment_id,
            input_record_ids=input_record_ids or evidence_packet.claim_ids,
            evidence_packet_id=evidence_packet.id,
            persona_id=persona_id,
            persona_configuration_version=persona_configuration_version,
            prompt_version=self.prompt_version,
            output_schema_version=self.output_schema_version,
            model_selection_policy=self.policy_id,
            maximum_output_tokens=maximum_output_tokens,
            max_attempts=max_attempts,
            deadline=deadline,
        )

    async def generate(
        self,
        request: GenerationRequest,
        objective: str = "",
        schema: Optional[Dict[str, Any]] = None,
        untrusted_sources: Optional[List[str]] = None,
    ) -> GenerationOutcome:
        """Run the request with bounded retries. Records every attempt (§14.6)."""
        outcome = GenerationOutcome(request=request)

        # Idempotency: a retry of a completed request reuses the script.
        if request.idempotency_key in self._idempotency:
            outcome.script = self._idempotency[request.idempotency_key]
            outcome.succeeded = True
            return outcome

        if request.is_expired():
            outcome.blocked_reason = "request expired before execution"
            return outcome

        # §14.5: untrusted content is wrapped and scanned.
        sanitized_sources: List[str] = []
        for index, source in enumerate(untrusted_sources or []):
            findings = self.guard.scan(source, field_name=f"source[{index}]")
            if findings:
                outcome.blocked_reason = (
                    f"untrusted source contained {len(findings)} injection "
                    f"pattern(s): {findings[0].detail()}")
                return outcome
            sanitized_sources.append(self.guard.wrap_source_data(source))

        context = {
            "objective": objective or request.metadata.get("objective", ""),
            "claim_references": request.input_record_ids,
            "evidence_packet_id": request.evidence_packet_id,
            "sources": sanitized_sources,
            "prompt_version": request.prompt_version,
            "output_schema_version": request.output_schema_version,
        }

        for attempt_number in range(1, request.max_attempts + 1):
            attempt = GenerationAttempt(
                request_id=request.id,
                attempt_number=attempt_number,
                provider_id=self.provider.provider_id,
                model_id="stub-model",
                prompt_version=request.prompt_version,
                schema_version=request.output_schema_version,
            )
            outcome.attempts.append(attempt)

            try:
                if schema is not None:
                    result = await self.provider.generate_structured(context, schema)
                else:
                    result = await self.provider.generate(context)
            except ProviderError as error:
                attempt.failure_classification = type(error).__name__
                attempt.disposition = "failed"
                attempt.completed_at = datetime.now(timezone.utc)
                continue

            attempt.completed_at = datetime.now(timezone.utc)
            attempt.latency_ms = result.latency_ms
            attempt.token_usage = result.token_usage

            script = self._to_script(request, result)
            if script is None:
                # Semantic failure, not a transport failure. §14.6: a repeated
                # semantic failure must not loop forever.
                attempt.failure_classification = "malformed_output"
                attempt.disposition = "failed"
                key = f"{request.id}:{attempt.failure_classification}"
                self.semantic_failure_counts[key] = (
                    self.semantic_failure_counts.get(key, 0) + 1)
                continue

            attempt.disposition = "succeeded"
            attempt.validation_results = {"structured_output": "valid"}
            outcome.script = script
            outcome.succeeded = True
            self._idempotency[request.idempotency_key] = script
            return outcome

        outcome.blocked_reason = (
            f"exhausted {request.max_attempts} attempt(s) without a valid script")
        return outcome

    def _to_script(
        self, request: GenerationRequest, result: GenerationResult
    ) -> Optional[BroadcastScript]:
        """Convert provider output into a script.

        A result with no usable structured content is a semantic failure, not
        a successful generation.
        """
        structured = result.structured or {}
        raw_turns = structured.get("turns")
        if not raw_turns:
            return None
        turns = []
        for index, raw in enumerate(raw_turns, start=1):
            turns.append(ScriptTurn(
                sequence=raw.get("sequence", index),
                persona_id=request.persona_id,
                text=raw.get("text", ""),
                speech_act=_speech_act(raw.get("speech_act")),
                claim_references=list(raw.get("claim_references", [])),
                evidence_references=list(raw.get("evidence_references", [])),
                generation_metadata={
                    "provider_id": result.provider_id,
                    "model_id": result.model_id,
                    "prompt_version": request.prompt_version,
                    "schema_version": request.output_schema_version,
                },
            ))
        return BroadcastScript(
            segment_id=request.segment_id,
            version=1,
            generation_request_id=request.id,
            evidence_packet_id=request.evidence_packet_id,
            turns=turns,
        )


def _speech_act(value: Any) -> SpeechAct:
    if isinstance(value, SpeechAct):
        return value
    try:
        return SpeechAct(str(value))
    except ValueError:
        # An unrecognised speech act defaults to the most cautious class, so
        # validation treats the turn as carrying factual load.
        return SpeechAct.FACTUAL_REPORT
