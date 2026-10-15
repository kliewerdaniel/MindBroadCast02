"""LLM-backed persona analysis: the missing hook in the §12.4 sequence.

The persona machinery (IndependentAnalysisRunner, DisagreementDetector,
DialogueComposer, DialogueOrchestrator) is complete and deterministic — but
its `AnalysisFunction` is a synchronous (config, packet) -> analysis with no
LLM. This module supplies that hook: given a persona's analytical preferences,
it asks the model for that persona's first-pass read of a story.

The §12.3 boundary is enforced in the prompt and checked after: a persona may
disagree about significance, mechanism, and implication, but must NOT
independently decide whether a factual proposition is true. The shared facts
are handed to every persona identically; the persona only shapes how it weighs
and connects them. The returned PersonaAnalysis records which facts it
accepted (all of them) and the assumptions behind its position, so a later
disagreement is attributable to a difference in reasoning, not evidence.

The analyses are produced independently (§12.4 step 3): the function sees only
the shared packet, never another persona's output. That ordering lives in
IndependentAnalysisRunner, not here — this just makes each call persona-flavored
and real.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..personas.entities import PersonaAnalysis, PersonaConfig
from ..personas.analysis import SharedEvidencePacket


@dataclass
class PersonaLens:
    """A persona's analytical angle, distilled for the prompt.

    These shape HOW a persona reads the facts — emphasis, questions, framing —
    never WHETHER a fact is true (§12.3). Distinct lenses are what produce
    genuine disagreement worth airing.
    """
    persona_id: str
    name: str
    role: str = ""
    emphasis: str = ""                 # what this persona weighs most
    questions: List[str] = field(default_factory=list)
    style: str = ""                    # vocabulary / tone


# A small default newsroom: three distinct editorial lenses. These are the
# substrate multi-persona broadcast runs on; each reads the same facts
# differently, which is where real analytical value comes from.
DEFAULT_LENSES: List[PersonaLens] = [
    PersonaLens(
        persona_id="analyst",
        name="The Analyst",
        role="structural interpretation",
        emphasis="causes, mechanisms, and second-order consequences",
        questions=["What mechanism drives this?", "What follows from it?"],
        style="measured, precise, avoids speculation",
    ),
    PersonaLens(
        persona_id="skeptic",
        name="The Skeptic",
        role="claims and evidence",
        emphasis="what is asserted vs. established, and what is still unknown",
        questions=["What is actually proven here?",
                   "What would change this conclusion?"],
        style="probing, cautious about certainty",
    ),
    PersonaLens(
        persona_id="historian",
        name="The Historian",
        role="context and precedent",
        emphasis="how this compares to prior episodes and longer trends",
        questions=["When has this happened before?",
                   "What does the pattern suggest?"],
        style="grounded in precedent, patient with detail",
    ),
]


def _extract_json(text: str) -> Optional[Any]:
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start = next((i for i, c in enumerate(candidate) if c in "{["), None)
    if start is None:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(candidate)):
        ch = candidate[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(candidate[start:j + 1])
                except ValueError:
                    return None
    return None


class LLMAnalysisFactory:
    """Builds a per-persona AnalysisFunction backed by the LLM.

    register(registry, lenses) wires each lens into an
    IndependentAnalysisRunner so the §12.4 sequence runs with real, distinct
    first passes. Each function is independent: it receives only the shared
    packet and the persona's lens.
    """

    def __init__(self, llm: Any, num_predict: int = 4096):
        self.llm = llm
        self.num_predict = num_predict

    def make_function(self, lens: PersonaLens):
        """An AnalysisFunction: (config, packet) -> PersonaAnalysis."""
        def analyze(config: PersonaConfig,
                    packet: SharedEvidencePacket) -> PersonaAnalysis:
            prompt = self._build_prompt(lens, packet)
            raw = self._call(prompt)
            return self._to_analysis(lens, packet, raw)
        return analyze

    def register(self, registry: Any, runner: Any,
                 lenses: Optional[List[PersonaLens]] = None) -> List[str]:
        """Register an analysis function per lens; return the persona ids.

        A PersonaConfig is registered in the registry so the orchestrator can
        resolve it, and the analysis function is registered on the runner.
        """
        lenses = lenses or DEFAULT_LENSES
        ids: List[str] = []
        for lens in lenses:
            config = PersonaConfig(
                id=lens.persona_id, name=lens.name, role=lens.role,
                description=lens.emphasis)
            registry.register(config)
            runner.register(lens.persona_id, self.make_function(lens))
            ids.append(lens.persona_id)
        return ids

    def _build_prompt(self, lens: PersonaLens,
                      packet: SharedEvidencePacket) -> str:
        facts = "\n".join(f"- {f}" for f in packet.facts) or "(none supplied)"
        questions = "\n".join(f"- {q}" for q in lens.questions) or "- none"
        return (
            f"You are {lens.name}, a {lens.role} on a news analysis desk.\n"
            f"Your analytical emphasis: {lens.emphasis}.\n"
            f"Speaking style: {lens.style}.\n\n"
            f"Established facts about the story (accept these as given; do "
            f"not re-decide whether they are true):\n{facts}\n\n"
            f"Give your first-pass read, weighing especially:\n{questions}\n\n"
            f"You may interpret significance, mechanism, and implication. Do "
            f"NOT introduce new facts, names, numbers, or events not listed "
            f"above. Note the assumptions your reading rests on.\n\n"
            f"Respond with JSON:\n"
            f'{{"position":"<1-2 sentences: your reading>",'
            f'"assumptions":["<assumption 1>","<assumption 2>"],'
            f'"confidence":0.7}}'
        )

    def _call(self, prompt: str) -> str:
        payload = {
            "model": getattr(self.llm, "model", "qwen3:30b-a3b"),
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.5, "num_predict": self.num_predict,
                        "think": False},
        }
        post = getattr(self.llm, "_post", None)
        if post is None:
            return ""
        try:
            data = post("/api/generate", payload)
        except Exception:
            return ""
        return (data.get("response", "")
                or (data.get("thinking", "")
                    if _extract_json(data.get("thinking", "")) else ""))

    def _to_analysis(self, lens: PersonaLens,
                     packet: SharedEvidencePacket, raw: str) -> PersonaAnalysis:
        obj = _extract_json(raw)
        position, assumptions, confidence = "", [], 0.5
        if isinstance(obj, dict):
            position = str(obj.get("position", "")).strip()
            assumptions = [str(a) for a in obj.get("assumptions", []) or []]
            try:
                confidence = float(obj.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5

        # §12.3 grounding floor: a persona may interpret significance, mechanism,
        # and implication, but must not introduce new facts. The prompt asks, but
        # the model may still invent (observed live: a historian citing named
        # 2016 figures absent from the facts). Deterministic check on the
        # bounded, checkable class of invention — a fabricated named entity or
        # figure — and drop the position if one appears, so it cannot air.
        # Interpretive vocabulary ("speculative", "mechanism") is legitimate
        # analysis and is not policed here.
        if position and packet.facts:
            from ..inference.editor import GroundingVerifier
            verifier = GroundingVerifier(packet.facts)
            reason = verifier.fabrication_reason(position)
            if reason:
                return PersonaAnalysis(
                    persona_id=lens.persona_id,
                    question=packet.question,
                    position="",                 # dropped: ungrounded
                    assumptions=[f"position dropped: {reason} (§12.3)"],
                    evidence_used=list(packet.evidence_references),
                    shared_facts_accepted=list(packet.facts),
                    confidence=0.0,
                )

        return PersonaAnalysis(
            persona_id=lens.persona_id,
            question=packet.question,
            position=position,
            assumptions=assumptions,
            evidence_used=list(packet.evidence_references),
            shared_facts_accepted=list(packet.facts),
            confidence=max(0.0, min(1.0, confidence)),
        )
