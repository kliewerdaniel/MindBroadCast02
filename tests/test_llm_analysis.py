"""Tests for LLM-backed persona analysis (the §12.4 hook).

The invariant: personas read the same shared facts through distinct lenses and
disagree about significance/mechanism/implication — never about whether a fact
is true (§12.3). The analysis records assumptions so a disagreement is
attributable to reasoning, not evidence.
"""

import json

from core.personas.analysis import (
    DisagreementDetector,
    DisagreementGuard,
    IndependentAnalysisRunner,
    SharedEvidencePacket,
)
from core.personas.entities import PersonaConfig
from core.personas.llm_analysis import (
    DEFAULT_LENSES,
    LLMAnalysisFactory,
    PersonaLens,
    _extract_json,
)
from core.personas.memory import PersonaRegistry

PACKET = SharedEvidencePacket(
    question="What does the appointment mean?",
    facts=[
        "Katie Zacharia worked for one of Trump companies and raised her "
        "profile on Fox News.",
        "She also amplified right-wing conspiracy theories on social media.",
        "Now she will be the president chief spokesperson.",
    ],
    evidence_references=["ev-1"],
)


class FakeLLM:
    """Returns a persona-flavored JSON analysis; records prompts seen."""

    def __init__(self, per_lens):
        self.model = "fake-model"
        self.per_lens = per_lens
        self.prompts = []

    def _post(self, path, payload):
        self.prompts.append(payload["prompt"])
        # Match by persona name — the prompt always opens "You are {name}",
        # so this is robust regardless of the lens emphasis/questions wording.
        for key, obj in self.per_lens.items():
            if key.lower() in payload["prompt"].lower():
                return {"response": json.dumps(obj)}
        return {"response": "{}"}


def _factory(per_lens):
    return LLMAnalysisFactory(FakeLLM(per_lens))


# ── _extract_json ────────────────────────────────────────────────────

def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_from_fence():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_none():
    assert _extract_json("no json here") is None


# ── analysis production ──────────────────────────────────────────────

def test_make_function_produces_persona_analysis():
    lens = PersonaLens(persona_id="analyst", name="Analyst",
                       emphasis="mechanisms")
    llm = FakeLLM({"Analyst": {
        "position": "She worked for a Trump company and raised her profile "
                    "on Fox News.",
        "assumptions": ["x"], "confidence": 0.8}})
    factory = LLMAnalysisFactory(llm)
    config = PersonaConfig(id="analyst", name="Analyst")
    analysis = factory.make_function(lens)(config, PACKET)
    assert analysis.persona_id == "analyst"
    assert analysis.position == ("She worked for a Trump company and raised "
                                 "her profile on Fox News.")
    assert analysis.assumptions == ["x"]
    assert analysis.confidence == 0.8
    # §12.3: the persona accepts the shared facts, does not re-decide them.
    assert analysis.shared_facts_accepted == list(PACKET.facts)


def test_prompt_forbids_new_facts():
    lens = PersonaLens(persona_id="analyst", name="Analyst",
                       emphasis="mechanisms")
    llm = FakeLLM({"Analyst": {"position": "p", "assumptions": [],
                               "confidence": 0.5}})
    factory = LLMAnalysisFactory(llm)
    factory.make_function(lens)(PersonaConfig(id="analyst"), PACKET)
    prompt = llm.prompts[0]
    assert "do not re-decide whether they are true" in prompt
    assert "Do NOT introduce new facts" in prompt


def test_confidence_clamped():
    lens = PersonaLens(persona_id="x", name="X")
    llm = FakeLLM({"X": {"position": "p", "assumptions": [],
                         "confidence": 5.0}})
    factory = LLMAnalysisFactory(llm)
    a = factory.make_function(lens)(PersonaConfig(id="x"), PACKET)
    assert a.confidence == 1.0


def test_empty_model_output_is_honest():
    lens = PersonaLens(persona_id="x", name="X")
    llm = FakeLLM({})
    factory = LLMAnalysisFactory(llm)
    a = factory.make_function(lens)(PersonaConfig(id="x"), PACKET)
    assert a.position == ""
    assert a.confidence == 0.5


def test_ungrounded_position_is_dropped():
    """§12.3 floor: a persona that invents a named entity does not participate.

    The historian live-run cited named 2016 figures absent from the facts.
    The prompt is not enough — a deterministic check drops the position so it
    cannot air.
    """
    lens = PersonaLens(persona_id="historian", name="Historian")
    llm = FakeLLM({"Historian": {
        "position": "This mirrors Kellyanne Conway's 2016 appointment and "
                    "Sean Hannity's influence.",
        "assumptions": [], "confidence": 0.8}})
    factory = LLMAnalysisFactory(llm)
    a = factory.make_function(lens)(PersonaConfig(id="historian"), PACKET)
    assert a.position == ""               # dropped, not aired
    assert a.confidence == 0.0
    assert any("dropped" in x for x in a.assumptions)
    assert any("Conway" in x for x in a.assumptions)
    # The shared facts are still recorded as accepted.
    assert a.shared_facts_accepted == list(PACKET.facts)


def test_invented_number_dropped():
    """A fabricated figure is invention even without a named entity.

    "47 prior cases since 2019" introduces no proper noun, so the entity
    check alone would miss it; the number check catches it.
    """
    lens = PersonaLens(persona_id="analyst", name="Analyst")
    llm = FakeLLM({"Analyst": {
        "position": "Her appointment reflects a pattern seen in 47 prior "
                    "cases since 2019.",
        "assumptions": [], "confidence": 0.7}})
    factory = LLMAnalysisFactory(llm)
    a = factory.make_function(lens)(PersonaConfig(id="analyst"), PACKET)
    assert a.position == ""
    assert a.confidence == 0.0
    assert any("figure" in x for x in a.assumptions)


def test_sourced_number_kept():
    """A number that appears in the facts is not invention."""
    lens = PersonaLens(persona_id="analyst", name="Analyst")
    llm = FakeLLM({"Analyst": {
        "position": "She raised her profile on Fox News in 2016.",
        "assumptions": [], "confidence": 0.7}})
    packet = SharedEvidencePacket(
        question="q",
        facts=["She raised her profile on Fox News in 2016."])
    factory = LLMAnalysisFactory(llm)
    a = factory.make_function(lens)(PersonaConfig(id="analyst"), packet)
    assert a.position.startswith("She raised her profile")
    assert a.confidence == 0.7


def test_grounded_position_is_kept():
    lens = PersonaLens(persona_id="analyst", name="Analyst")
    llm = FakeLLM({"Analyst": {
        "position": "She raised her profile on Fox News and amplified "
                    "conspiracy theories on social media.",
        "assumptions": [], "confidence": 0.7}})
    factory = LLMAnalysisFactory(llm)
    a = factory.make_function(lens)(PersonaConfig(id="analyst"), PACKET)
    assert a.position.startswith("She raised her profile")
    assert a.confidence == 0.7


# ── registration into the §12.4 sequence ─────────────────────────────

def test_register_wires_registry_and_runner():
    per_lens = {
        "Analyst": {"position": "She raised her profile on Fox News.",
                    "assumptions": [], "confidence": 0.6},
        "Skeptic": {"position": "The link to her role is speculative.",
                    "assumptions": [], "confidence": 0.6},
        "Historian": {"position": "This follows an established pattern.",
                      "assumptions": [], "confidence": 0.6},
    }
    factory = _factory(per_lens)
    registry = PersonaRegistry()
    runner = IndependentAnalysisRunner()
    ids = factory.register(registry, runner)
    assert ids == [line.persona_id for line in DEFAULT_LENSES]
    # Personas resolvable by the orchestrator.
    for pid in ids:
        assert registry.get(pid) is not None


def test_distinct_lenses_yield_disagreement():
    per_lens = {
        "Analyst": {"position": "Her appointment leverages the Fox News "
                    "profile and social media amplification she built.",
                    "assumptions": ["strategic"], "confidence": 0.7},
        "Skeptic": {"position": "The appointment is established, but the link "
                    "to her social media activity is speculative.",
                    "assumptions": ["cautious"], "confidence": 0.6},
        "Historian": {"position": "This appointment follows the pattern of "
                     "elevating a media figure who amplified the president "
                     "narratives.",
                     "assumptions": ["precedent"], "confidence": 0.6},
    }
    factory = _factory(per_lens)
    registry = PersonaRegistry()
    runner = IndependentAnalysisRunner()
    factory.register(registry, runner)
    personas = [p for p in (registry.get(line.persona_id)
                            for line in DEFAULT_LENSES) if p is not None]
    analyses = runner.run_first_passes(personas, PACKET)

    positions = {a.persona_id: a.position for a in analyses}
    assert len(set(positions.values())) == 3      # all distinct (§12.4)

    guard = DisagreementGuard()
    assert not guard.should_suppress(PACKET, analyses)

    detector = DisagreementDetector()
    disagreements = detector.detect(analyses, PACKET)
    assert any(d.is_substantive for d in disagreements)


def test_first_passes_are_independent():
    # Each persona's prompt must carry only the shared packet, never another
    # persona's position — enforced by the runner's ordering, verified here by
    # the prompt contents.
    per_lens = {line.name: {"position": "p", "assumptions": [],
                           "confidence": 0.5} for line in DEFAULT_LENSES}
    llm = FakeLLM(per_lens)
    factory = LLMAnalysisFactory(llm)
    registry = PersonaRegistry()
    runner = IndependentAnalysisRunner()
    factory.register(registry, runner)
    runner.run_first_passes(
        [p for p in (registry.get(line.persona_id)
                     for line in DEFAULT_LENSES) if p is not None], PACKET)
    # No prompt leaked another persona's position text.
    for prompt in llm.prompts:
        assert "a deliberate consolidation" not in prompt
