"""Tests for the editor agent: structured composition + grounding floor.

The invariant under test: the editor may structure grounded facts into a
bulletin, but nothing unsupported may air. The model proposes; the
deterministic verifier decides (§22.2).
"""

from core.inference.editor import (
    EditorialComposer,
    GroundingVerifier,
    _stem,
)

FACTS = [
    "Katie Zacharia worked for one of Trump companies and raised her "
    "profile on Fox News.",
    "She also amplified right-wing conspiracy theories on social media.",
    "Now she will be the president chief spokesperson.",
    "The appointment takes effect at the start of next month.",
]


# ── _stem ────────────────────────────────────────────────────────────

def test_stem_folds_company_plural():
    assert _stem("company") == _stem("companies")


def test_stem_folds_verb_inflection():
    assert _stem("raise") == _stem("raised")
    assert _stem("work") == _stem("worked")


def test_stem_keeps_distinct_roots_apart():
    assert _stem("musk") != _stem("company")
    assert _stem("million") != _stem("spokesperson")


# ── GroundingVerifier ────────────────────────────────────────────────

def test_accepts_faithful_paraphrase():
    v = GroundingVerifier(FACTS)
    assert v.is_grounded("She worked for one of the Trump companies.")


def test_accepts_connectives():
    v = GroundingVerifier(FACTS)
    assert v.is_grounded("Meanwhile, she worked for a Trump company.")


def test_rejects_invented_number():
    v = GroundingVerifier(FACTS)
    assert not v.is_grounded("She earned 5 million dollars last year.")


def test_rejects_invented_entity():
    v = GroundingVerifier(FACTS)
    assert not v.is_grounded("She met with Elon Musk in Texas.")


def test_rejects_invented_role():
    v = GroundingVerifier(FACTS)
    assert not v.is_grounded("She is the vice president of the company.")


def test_rejects_empty():
    v = GroundingVerifier(FACTS)
    assert not v.is_grounded("")


# ── EditorialComposer ────────────────────────────────────────────────

def test_compose_keeps_grounded_turns():
    comp = EditorialComposer(llm=None)
    raw = ('{"turns":[{"sequence":1,"text":"She worked for one of the '
           'Trump companies.","speech_act":"factual_report"}]}')
    plan = comp.compose(raw, FACTS)
    assert len(plan.turns) == 1
    assert plan.dropped_turns == 0
    assert plan.grounded


def test_compose_drops_ungrounded_turns():
    comp = EditorialComposer(llm=None)
    raw = ('{"turns":['
           '{"sequence":1,"text":"She worked for a Trump company.",'
           '"speech_act":"factual_report"},'
           '{"sequence":2,"text":"She met Elon Musk and earned 5 million.",'
           '"speech_act":"analysis"}]}')
    plan = comp.compose(raw, FACTS)
    assert len(plan.turns) == 1          # only the grounded one survives
    assert plan.dropped_turns == 1
    assert plan.grounded                 # some turns survived


def test_compose_all_ungrounded_falls_back():
    comp = EditorialComposer(llm=None)
    raw = ('{"turns":[{"sequence":1,"text":"She met Elon Musk in Texas.",'
           '"speech_act":"analysis"}]}')
    plan = comp.compose(raw, FACTS)
    assert len(plan.turns) == 0
    assert plan.dropped_turns == 1
    assert not plan.grounded             # caller falls back to flat rewrite


def test_compose_handles_malformed_json():
    comp = EditorialComposer(llm=None)
    plan = comp.compose("not json at all", FACTS)
    assert plan.turns == []
    assert not plan.grounded


def test_compose_tolerates_code_fences():
    comp = EditorialComposer(llm=None)
    raw = ('```json\n{"turns":[{"sequence":1,"text":"She worked for a '
           'Trump company.","speech_act":"factual_report"}]}\n```')
    plan = comp.compose(raw, FACTS)
    assert len(plan.turns) == 1


def test_build_prompt_numbers_the_facts():
    comp = EditorialComposer(llm=None)
    prompt = comp.build_prompt(FACTS)
    assert "1. Katie Zacharia" in prompt
    assert "Use ONLY the facts given" in prompt
