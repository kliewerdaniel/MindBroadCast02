"""The editor agent: structure grounded facts into a real broadcast.

The flat per-fact rewrite (runtime._produce_segment) speaks each fact once,
in order — faithful but list-like. This module adds the layer above it: an
LLM-driven editor that takes the story's grounded, deduplicated facts and
arranges them into a structured bulletin (context -> developments ->
significance), while staying strictly grounded so it cannot invent.

Design boundary (§14.3/§14.4): the editor is given the facts and nothing else.
It may reorder, connect, and attribute, but every clause must trace to a
supplied fact. The deterministic verifier below enforces that — it is the
floor, not the model's good behavior.

Why a verifier and not trust (§22.2): the model proposes, deterministic code
decides. If the editor emits a sentence that introduces an entity or number
not present in the source facts, that turn is dropped and the story falls back
to the faithful flat rewrite. A partially-grounded bulletin is worse than a
plain one; the whole point is that nothing airs unsupported.

This is the single-voice substrate that a multi-persona composer later sits on
(core/personas already has the independent-analysis and disagreement
machinery; this adds the editorial structuring pass it lacks).
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EditorialPlan:
    """A structured bulletin: ordered turns, each traced to source facts."""
    turns: List[Dict[str, Any]] = field(default_factory=list)
    grounded: bool = True
    dropped_turns: int = 0
    notes: str = ""


def _content_tokens(text: str) -> set:
    """Lowercased alphanumeric tokens, minus trivial function words."""
    stop = set("the a an and or but of to in on for with is are was were be "
               "been it its this that will now as at by from has have had "
               "not no also into over after before about he she they".split())
    return {w for w in re.findall(r"[a-z0-9]+", text.lower())
            if w not in stop and len(w) > 1}


def _numbers(text: str) -> set:
    """Numeric tokens — a fabricated figure is the most dangerous invention."""
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def _stem(word: str) -> str:
    """Crude stem: strip common English inflectional suffixes.

    Not a real stemmer — just enough to treat "boosted"/"boost",
    "companies"/"company", "raises"/"raised" as the same word so the grounding
    verifier accepts faithful paraphrase while still rejecting genuinely new
    vocabulary. Deliberately conservative: it never maps two different roots
    together, so it cannot launder an invented term.
    """
    # "company"/"companies" must share a stem. Fold a trailing y to i first so
    # the singular and the -ies plural converge on the same key.
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "i"          # companies -> compani
    if word.endswith("y") and len(word) > 3:
        return word[:-1] + "i"          # company -> compani
    for suffix in ("ing", "ed", "es", "s", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]    # raised -> rais
    # Fold a trailing silent e so "raise" -> "rais" matches "raised" -> "rais"
    # (and "amplify"/"amplified" converge the same way via the y-fold above).
    if word.endswith("e") and len(word) > 3:
        return word[:-1]                  # raise -> rais
    return word


class GroundingVerifier:
    """Deterministic check that a turn adds nothing unsupported.

    A turn is grounded when every content word and every number it uses also
    appears in the union of the source facts. Connecting prose ("meanwhile",
    "according to reports") is allowed via a small allowlist of discourse
    words the editor may introduce; everything else must be sourced. This is
    deliberately strict — it is the epistemic floor (§14.3), and a dropped
    turn is recovered by the faithful flat rewrite.
    """

    # Discourse/connective words an editor may add to shape narration without
    # introducing new claims.
    ALLOWED_CONNECTIVES = {
        "meanwhile", "however", "moreover", "according", "reports",
        "reportedly", "additionally", "furthermore", "also", "now", "then",
        "later", "earlier", "previously", "following", "amid", "despite",
        "while", "when", "where", "who", "which", "that", "said", "says",
        "told", "added", "noted", "confirmed", "announced", "mean", "means",
    }

    def __init__(self, facts: List[str]):
        self.source_tokens: set = set()
        self.source_numbers: set = set()
        for f in facts:
            self.source_tokens |= _content_tokens(f)
            self.source_numbers |= _numbers(f)

    def is_grounded(self, turn_text: str) -> bool:
        text = turn_text.strip()
        if not text:
            return False
        # Numbers must all appear in the source — a new figure is invention.
        if not _numbers(text).issubset(self.source_numbers):
            return False
        novel = _content_tokens(text) - self.source_tokens - self.ALLOWED_CONNECTIVES
        # Accept morphological variants of sourced words (plurals, verb
        # endings, boosted/raises): a token is allowed if it shares a stem
        # with any source token. Compare on a crude stem (strip common
        # suffixes) so "boosted"/"boost" and "companies"/"company" match,
        # while genuinely new words ("musk", "million") still fail.
        source_stems = {_stem(s) for s in self.source_tokens}
        for tok in novel:
            if _stem(tok) not in source_stems:
                return False
        return True


class EditorialComposer:
    """Turns grounded facts into a structured bulletin via the LLM.

    Composes (does not discover): the facts are fixed inputs; the editor only
    orders and connects them. Every emitted turn is verified against the
    source facts, and ungrounded turns are dropped. If nothing survives, the
    caller falls back to the flat faithful rewrite.
    """

    SYSTEM = (
        "You are the editor of a news broadcast. You are given a numbered list "
        "of established facts about one story. Write a short spoken bulletin "
        "that reports those facts in a natural order: open with the most "
        "newsworthy fact, then add supporting detail, then close on where the "
        "story stands.\n\n"
        "Hard rules — you are composing, not discovering:\n"
        "- Use ONLY the facts given. Do not add names, numbers, dates, roles, "
        "causes, or quotes that are not in the facts.\n"
        "- You may connect facts with ordinary narration (meanwhile, however, "
        "according to the report) but every claim must trace to a fact.\n"
        "- Do not repeat a fact. Each fact appears at most once.\n"
        "- Keep each sentence short enough to be spoken aloud.\n"
    )

    def __init__(self, llm: Any):
        self.llm = llm

    def build_prompt(self, facts: List[str]) -> str:
        numbered = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts))
        return (
            f"{self.SYSTEM}\n"
            f"Facts:\n{numbered}\n\n"
            f"Respond with JSON like this example:\n"
            f'{{"turns":[{{"sequence":1,"text":"<one spoken sentence>",'
            f'"speech_act":"factual_report"}},{{"sequence":2,'
            f'"text":"<another spoken sentence>",'
            f'"speech_act":"analysis"}}]}}'
        )

    def compose(self, raw_text: str, facts: List[str]) -> EditorialPlan:
        """Parse the model's turns and keep only the grounded ones.

        Deterministic post-processing: the model proposes the structure, this
        decides what is safe to air (§22.2).
        """
        plan = EditorialPlan()
        structured = _extract_json_safe(raw_text)
        raw_turns = []
        if isinstance(structured, dict):
            raw_turns = structured.get("turns", []) or []
        else:
            # No parseable script at all: signal unusable so the caller falls
            # back to the faithful flat rewrite rather than airing silence.
            plan.grounded = False
            plan.notes = "no parseable script from the editor"
            return plan
        verifier = GroundingVerifier(facts)

        for turn in raw_turns:
            if not isinstance(turn, dict):
                continue
            text = str(turn.get("text", "")).strip()
            if not text:
                continue
            if verifier.is_grounded(text):
                plan.turns.append({
                    "sequence": len(plan.turns) + 1,
                    "text": text,
                    "speech_act": turn.get("speech_act", "factual_report"),
                })
            else:
                plan.dropped_turns += 1

        if plan.dropped_turns and not plan.turns:
            plan.grounded = False
            plan.notes = (f"all {plan.dropped_turns} composed turns were "
                          f"ungrounded; falling back to flat rewrite")
        elif plan.dropped_turns:
            plan.notes = f"dropped {plan.dropped_turns} ungrounded turn(s)"
        return plan


def _extract_json_safe(text: str) -> Optional[Any]:
    """First balanced JSON object/array in text, tolerating fences/prose."""
    if not text:
        return None
    # Strip code fences if present.
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start = None
    for i, ch in enumerate(candidate):
        if ch in "{[":
            start = i
            break
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
                import json
                try:
                    return json.loads(candidate[start:j + 1])
                except ValueError:
                    return None
    return None
