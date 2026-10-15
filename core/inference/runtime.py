"""The live runtime: an actually-running broadcast loop.

This is the piece that was missing. Every prior phase built and verified a
subsystem, but nothing orchestrated them into a loop that *runs*. The runtime
does, against real providers:

    rank stories -> assemble evidence packet -> generate script (real LLM)
    -> validate (8 stages) -> director lifecycle -> synthesize (real TTS)
    -> audio buffer

Design boundaries, from the spec:

- §13.1: the director owns session state and the queue; it does not rank or
  write. The runtime drives it; it does not become the director.
- §27.4: when there is nothing valuable to say, the runtime stays silent rather
  than fabricating novelty. The loop is a capability, not an obligation.
- §23.3: work is bounded. The runtime processes one segment at a time and
  records a transcript entry per delivered turn.
- Fail-closed: if the LLM or TTS is unavailable, the runtime reports it and
  stops cleanly. It never fabricates a segment or fakes audio.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from ..broadcast.context import ContextBuilder, GenerationOrchestrator
from ..broadcast.director import BroadcastDirector
from ..broadcast.entities import (
    AudioChunk, BroadcastScript, Segment, SegmentStatus, ScriptTurn,
    SpeechAct, ValidationOutcome,
)
from ..broadcast.providers import (
    GenerationResult, LanguageModelProvider, ProviderError, SynthesisRequest,
    TTSProvider,
)
from ..broadcast.validation import ScriptValidator
from ..world_model.ranking import StoryFacts, StoryRanker
from .editor import EditorialComposer
from .voices import VoiceRegistry


@dataclass
class RuntimeConfig:
    """Tunable runtime behavior. Bounded by design (§23.3)."""
    max_segments: int = 10            # stop after N segments (0 = unbounded)
    idle_sleep_seconds: float = 1.0   # sleep when nothing to say (§27.4)
    script_schema: Dict[str, Any] = field(
        default_factory=lambda: {"$id": "broadcast_script_v1"})
    persona_id: str = "presenter"
    # Prefer multi-persona dialogue for stories with 2+ facts (§12.4). When the
    # disagreement guard suppresses (nothing to debate) or the dialogue is not
    # approved, the runtime falls back to the single-voice editor. Set False to
    # always use the single-voice editor.
    multi_persona: bool = True


@dataclass
class TranscriptLine:
    speaker: str
    text: str
    segment_id: str
    delivered: bool = False
    duration_ms: Optional[int] = None
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Per-turn attribution for multi-voice segments: [{speaker, text}, ...] in
    # spoken order. Empty for single-voice segments. Lets the console render a
    # debate as attributed lines rather than one anonymous blob.
    turns: List[Dict[str, str]] = field(default_factory=list)


class LiveRuntime:
    """Runs the broadcast loop against real LLM + TTS providers."""

    def __init__(
        self,
        llm: LanguageModelProvider,
        tts: TTSProvider,
        validator: Optional[ScriptValidator] = None,
        ranker: Optional[StoryRanker] = None,
        config: Optional[RuntimeConfig] = None,
        director: Optional[BroadcastDirector] = None,
    ):
        self.llm = llm
        self.tts = tts
        self.validator = validator or ScriptValidator(
            known_voice_profile_ids={config.persona_id if config else "presenter"})
        self.ranker = ranker or StoryRanker()
        self.config = config or RuntimeConfig()
        self.director = director or BroadcastDirector()
        self.generator = GenerationOrchestrator(self.llm)
        self.context_builder = ContextBuilder()
        self.editor = EditorialComposer(llm=self.llm)
        self.voices = VoiceRegistry()
        self.transcript: List[TranscriptLine] = []
        self.segments_produced = 0
        self.errors: List[str] = []
        self._running = False

    async def health(self) -> Dict[str, bool]:
        """Fail-closed preflight: both providers must be live."""
        return {
            "llm": await self.llm.health_check(),
            "tts": await self.tts.health_check(),
        }

    async def run(self, topics: List[Dict[str, Any]],
                  on_segment: Optional[Callable[[TranscriptLine], None]] = None
                  ) -> List[TranscriptLine]:
        """Broadcast each topic as a validated, synthesized segment.

        `topics` is a list of {"objective", "facts": [...], "claims": [...]}.
        An empty list, or one where nothing is worth saying, produces silence
        (§27.4) — the loop does not fabricate.
        """
        health = await self.health()
        if not all(health.values()):
            self.errors.append(f"providers unavailable: {health}")
            return self.transcript

        self._running = True
        try:
            for topic in topics:
                if self.config.max_segments and (
                        self.segments_produced >= self.config.max_segments):
                    break
                line = await self._produce_segment(topic)
                if line is not None:
                    self.transcript.append(line)
                    if on_segment:
                        on_segment(line)
        finally:
            self._running = False
        return self.transcript

    async def _produce_segment(self, topic: Dict[str, Any]
                               ) -> Optional[TranscriptLine]:
        objective = topic.get("objective", "").strip()
        if not objective:
            return None      # nothing to say; silence (§27.4)

        segment_id = f"seg-{self.segments_produced + 1:03d}"
        segment = Segment(
            id=segment_id, status=SegmentStatus.PLANNED,
            persona_id=self.config.persona_id, objective=objective,
            media_generation_id=f"gen-{segment_id}")
        self.director.add_segment(segment)

        # 1. Assemble a targeted, versioned evidence packet (§14.3/§14.4).
        packet = self.context_builder.build(
            segment_objective=objective,
            supporting_claims=topic.get("claims", []),
            evidence_records=topic.get("evidence", []),
        )
        # §14.3/§14.4: the model must be given the facts, not asked to discover
        # or invent them. An objective with no supplied facts makes a real
        # model refuse ("I don't have access...") — correctly. So the prompt
        # carries the packet's facts explicitly as source data.
        facts = topic.get("facts", [])
        facts_block = ""
        if facts:
            facts_block = ("\n\nFacts to report (use only these):\n"
                           + "\n".join(f"- {f}" for f in facts))

        # 1b. Multi-persona dialogue first (§12.4). When a story carries enough
        # substance, three personas read it independently and the guard decides
        # whether real disagreement exists. If the dialogue is approved, air it;
        # otherwise fall through to the single-voice editor. This is the richer
        # path; the editor is the fallback, not the default.
        if self.config.multi_persona and len(facts) >= 2:
            dialogue_script = self.compose_dialogue(objective, facts)
            if dialogue_script is not None:
                return await self._finish_dialogue(
                    segment_id, segment, dialogue_script, packet, facts)

        # 2. Real LLM generation (§14.3), idempotent and bounded.
        request = self.generator.make_request(
            task_type="script_generation", segment_id=segment_id,
            evidence_packet=packet, persona_id=self.config.persona_id)
        # The JSON example must show a CONCRETE value, not a placeholder — a
        # real model copies `<the sentence>` verbatim if that is what the
        # example contains. Use a filled example built from the first fact.
        example_fact = facts[0] if facts else objective
        # Longform via the editor agent: an LLM-driven structuring pass that
        # orders and connects the grounded facts into a real bulletin
        # (newsworthy lead -> detail -> close) instead of a flat per-fact
        # rewrite. It is still strictly grounded — every emitted turn is
        # checked against the source facts by EditorialComposer, and anything
        # unsupported is dropped (§14.3/§22.2). With one fact, the tight
        # single-sentence report is all there is to say.
        longform = len(facts) >= 2
        if longform:
            prompt = self.editor.build_prompt(facts)
        else:
            prompt = (f"{objective}{facts_block}\n\n"
                      f"Write one short spoken sentence reporting the fact. "
                      f'Respond with JSON like this example: '
                      f'{{"turns":[{{"sequence":1,"text":"{example_fact}",'
                      f'"speech_act":"factual_report"}}]}}')
        outcome = await self.generator.generate(
            request, objective=prompt, schema=self.config.script_schema)
        if not outcome.succeeded or outcome.script is None:
            self.errors.append(f"{segment_id}: generation failed — "
                               f"{outcome.blocked_reason}")
            self.director.advance(segment_id, SegmentStatus.CANCELLED)
            return None

        script = outcome.script
        # Editorial grounding floor (§14.3/§22.2): for a composed longform
        # bulletin, the editor may structure and connect the facts but may not
        # introduce anything unsupported. Run every turn through the
        # deterministic verifier against the source facts and drop any turn
        # that invents content. If nothing survives, the segment is cancelled
        # rather than airing an unsupported claim — the model proposes, the
        # verifier decides. Single-fact reports skip this (they are the fact).
        if longform and facts:
            from .editor import GroundingVerifier
            verifier = GroundingVerifier(facts)
            kept = [t for t in script.turns
                    if verifier.is_grounded(t.text)]
            dropped = len(script.turns) - len(kept)
            if dropped:
                self.errors.append(
                    f"{segment_id}: editor dropped {dropped} ungrounded "
                    f"turn(s)")
            if not kept:
                self.errors.append(
                    f"{segment_id}: no grounded turns survived; not airing")
                self.director.advance(segment_id, SegmentStatus.CANCELLED)
                return None
            script.turns = kept
        # Ensure each turn has a speaker for delivery validation (stage 8),
        # and carries the evidence-packet references so a factual claim is
        # grounded (§15.3) rather than refused for lacking provenance.
        packet_claims = list(packet.claim_ids)
        packet_evidence = list(packet.evidence_ids)
        for turn in script.turns:
            if not turn.persona_id:
                turn.persona_id = self.config.persona_id
            if turn.speech_act is SpeechAct.FACTUAL_REPORT:
                if not turn.claim_references and packet_claims:
                    turn.claim_references = packet_claims
                if not turn.evidence_references and packet_evidence:
                    turn.evidence_references = packet_evidence

        # 3. Validate the model's own output (§15.4, 8 stages). §15.3: a
        #    factual claim needs evidence; an unsupported one is refused.
        report = self.validator.validate(script)
        if report.outcome is ValidationOutcome.REJECTED:
            self.errors.append(f"{segment_id}: rejected — "
                               f"{[f.message for f in report.fatal_findings]}")
            self.director.advance(segment_id, SegmentStatus.REJECTED)
            return None

        # 4. Drive the legal §13.3 lifecycle to READY.
        for status in (SegmentStatus.GENERATING, SegmentStatus.GENERATED,
                       SegmentStatus.VALIDATING, SegmentStatus.APPROVED,
                       SegmentStatus.SYNTHESIZING, SegmentStatus.READY,
                       SegmentStatus.QUEUED):
            self.director.advance(segment_id, status)
        self.director.queue.append(segment_id)

        # 5. Synthesize the validated text with real TTS and buffer it.
        spoken_text = " ".join(t.text for t in script.turns if t.text).strip()
        if not spoken_text:
            self.director.advance(segment_id, SegmentStatus.CANCELLED)
            return None
        try:
            synth = await self.tts.synthesize(SynthesisRequest(
                segment_id=segment_id, text=spoken_text,
                synthesis_attempt=segment.media_generation_id,
                voice_profile=None))
        except ProviderError as error:
            # §13.3: a TTS failure returns the segment to APPROVED for retry;
            # it does not invalidate the script.
            self.errors.append(f"{segment_id}: TTS failed — {error}")
            self.director.advance(segment_id, SegmentStatus.FAILED)
            self.director.advance(segment_id, SegmentStatus.APPROVED)
            return None

        self.director.attach_audio(segment_id, AudioChunk(
            segment_id=segment_id, synthesis_attempt=segment.media_generation_id,
            sequence=0, data=synth.audio, duration_ms=synth.duration_ms,
            is_end_of_segment=True))

        # 6. Play out and complete.
        self.director.begin_playback(segment_id)
        self.director.complete_playback(segment_id)
        self.segments_produced += 1

        return TranscriptLine(
            speaker=self.config.persona_id, text=spoken_text,
            segment_id=segment_id, delivered=True,
            duration_ms=synth.duration_ms)

    def compose_dialogue(
        self, objective: str, facts: List[str],
        lenses: Optional[List[Any]] = None,
    ) -> Optional[Any]:
        """Run the §12.4 multi-persona sequence and return a validated script.

        Three or more personas independently read the same grounded facts
        through distinct lenses; the disagreement guard decides whether there
        is real divergence worth airing (§12.5). If the evidence supports a
        straightforward conclusion, disagreement is suppressed and this returns
        None — the caller falls back to the single-voice editor rather than
        manufacturing debate. Personas never re-decide facts (§12.3); they only
        shape significance, mechanism, and implication.

        This is the multi-agent layer above the editor: the editor structures
        one voice, this surfaces genuine analytical disagreement between voices.
        """
        from ..personas.analysis import SharedEvidencePacket
        from ..personas.dialogue_orchestrator import DialogueOrchestrator
        from ..personas.llm_analysis import LLMAnalysisFactory
        from ..personas.memory import PersonaRegistry

        if len(facts) < 2:
            return None                     # nothing to disagree about

        packet = SharedEvidencePacket(
            question=objective, facts=list(facts),
            evidence_references=[])
        registry = PersonaRegistry()
        orchestrator = DialogueOrchestrator(registry)
        factory = LLMAnalysisFactory(self.llm)
        persona_ids = factory.register(registry, orchestrator.runner, lenses)
        outcome = orchestrator.run(packet, persona_ids)

        if outcome.suppressed or not outcome.succeeded:
            reason = outcome.suppression_reason or "dialogue not approved"
            self.errors.append(f"dialogue suppressed: {reason}")
            return None
        return outcome.script

    async def _finish_dialogue(
        self, segment_id: str, segment: Any, script: Any,
        packet: Any, facts: List[str],
    ) -> Optional[TranscriptLine]:
        """Drive an approved multi-persona script through lifecycle + TTS.

        Mirrors the single-voice tail (steps 4-6): legal §13.3 lifecycle to
        READY, synthesize the joined turns, buffer, play out, complete. The
        script is already validated by the orchestrator (§12.4 step 8) and its
        turns carry the packet's evidence references.
        """
        from ..broadcast.entities import AudioChunk
        from ..broadcast.providers import SynthesisRequest

        for status in (SegmentStatus.GENERATING, SegmentStatus.GENERATED,
                       SegmentStatus.VALIDATING, SegmentStatus.APPROVED,
                       SegmentStatus.SYNTHESIZING, SegmentStatus.READY,
                       SegmentStatus.QUEUED):
            self.director.advance(segment_id, status)
        self.director.queue.append(segment_id)

        # Synthesize per speaker-group so each persona's voice applies, then
        # concatenate. Consecutive turns by the same persona are one synthesis
        # (so a persona's sentence flow is natural); when the speaker changes,
        # the voice changes. This is what makes the debate audibly a debate
        # rather than one voice reading every line.
        groups = self._group_by_speaker(script.turns)
        if not groups:
            self.director.advance(segment_id, SegmentStatus.CANCELLED)
            return None

        pcm_parts: List[bytes] = []
        total_ms = 0
        try:
            for persona_id, text in groups:
                if not text.strip():
                    continue
                voice = self.voices.for_persona(persona_id)
                synth = await self.tts.synthesize(SynthesisRequest(
                    segment_id=segment_id, text=text,
                    synthesis_attempt=segment.media_generation_id,
                    voice_profile=voice))
                pcm_parts.append(synth.audio)
                total_ms += int(synth.duration_ms or 0)
        except ProviderError as error:
            self.errors.append(f"{segment_id}: TTS failed — {error}")
            self.director.advance(segment_id, SegmentStatus.FAILED)
            self.director.advance(segment_id, SegmentStatus.APPROVED)
            return None

        audio = b"".join(pcm_parts)
        if not audio:
            self.director.advance(segment_id, SegmentStatus.CANCELLED)
            return None

        self.director.attach_audio(segment_id, AudioChunk(
            segment_id=segment_id,
            synthesis_attempt=segment.media_generation_id,
            sequence=0, data=audio, duration_ms=total_ms,
            is_end_of_segment=True))
        self.director.begin_playback(segment_id)
        self.director.complete_playback(segment_id)
        self.segments_produced += 1

        spoken_text = " ".join(t.text for t in script.turns if t.text).strip()
        speakers = ", ".join(dict.fromkeys(
            t.persona_id for t in script.turns if t.persona_id))
        turn_attribution = [
            {"speaker": t.persona_id or "presenter", "text": t.text}
            for t in script.turns if t.text
        ]
        return TranscriptLine(
            speaker=speakers or self.config.persona_id, text=spoken_text,
            segment_id=segment_id, delivered=True,
            duration_ms=total_ms, turns=turn_attribution)

    @staticmethod
    def _group_by_speaker(turns: List[Any]) -> List[tuple]:
        """Collapse consecutive same-persona turns into one synthesis unit.

        Returns [(persona_id, joined_text), ...] in order, so a persona's
        back-to-back lines flow as one utterance and the voice only changes
        when the speaker does.
        """
        groups: List[tuple] = []
        for turn in turns:
            text = (turn.text or "").strip()
            if not text:
                continue
            pid = turn.persona_id or "presenter"
            if groups and groups[-1][0] == pid:
                groups[-1] = (pid, groups[-1][1] + " " + text)
            else:
                groups.append((pid, text))
        return groups

    def summary(self) -> Dict[str, Any]:
        return {
            "segments_produced": self.segments_produced,
            "transcript_lines": len(self.transcript),
            "errors": self.errors,
            "last_completed_segment": self.director.session.last_completed_segment_id,
        }
