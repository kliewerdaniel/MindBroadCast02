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
from typing import Any


@dataclass
class EditorialPlan:
    """A structured bulletin: ordered turns, each traced to source facts."""
    turns: list[dict[str, Any]] = field(default_factory=list)
    grounded: bool = True
    dropped_turns: int = 0
    notes: str = ""


def _content_tokens(text: str) -> set:
    """Lowercased alphanumeric tokens, minus trivial function words."""
    stop = set(["the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
        is", "are", "was", "were", "be", "been", "it", "its", "this", "that", "will", "now", "as", "at", "by", "from", "has", "have", "had", "not", "no", "also", "into", "over", "after", "before", "about", "he", "she", "they"])
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

    # Analytical vocabulary: words about REASONING (mechanism, significance,
    # uncertainty), not about the world. A persona must be able to say a link
    # is "speculative" or call something a "pattern" without that counting as
    # an invented fact — those describe the analysis, not new entities. Kept
    # strictly separate from entities/numbers, which remain checked exactly:
    # a fabricated name or figure is still rejected.
    ANALYTICAL_VOCAB = {
        "mechanism", "leverages", "leverage", "speculative", "speculation",
        "pattern", "patterns", "precedent", "trend", "trends", "significance",
        "significant", "implication", "implications", "suggests", "suggest",
        "indicates", "indicate", "reflects", "reflect", "follows", "follow",
        "consistent", "mirrors", "mirror", "echoes", "parallel", "parallels",
        "causal", "cause", "consequence", "consequences", "second-order",
        "established", "unproven", "proven", "evidence", "link", "linked",
        "connection", "correlation", "strategic", "deliberate",
        "appears", "seems", "likely", "possibly", "perhaps", "arguably",
        "becoming", "built", "activity", "activities", "role", "roles",
        "amplification", "channels", "channel", "narrative", "narratives",
        "consolidation", "consolidates", "institutional", "communication",
        "normalize", "extending", "influence", "worker", "figure", "figures",
        "elevating", "elevated", "elevate", "appointing", "appointed",
        "placing", "placed", "consolidating", "reinforce", "reinforces",
        "reinforcing", "institutionalize", "normalizing",
    }

    def __init__(self, facts: list[str]):
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
        novel = _content_tokens(text) - self.source_tokens - self.ALLOWED_CONNECTIVES -
            elf.ANALYTICAL_VOCAB
        # Accept morphological variants of sourced words (plurals, verb
        # endings, boosted/raises): a token is allowed if it shares a stem
        # with any source token. Compare on a crude stem (strip common
        # suffixes) so "boosted"/"boost" and "companies"/"company" match,
        # while genuinely new words ("musk", "million") still fail.
        source_stems = {_stem(s) for s in self.source_tokens}
        return all(_stem(tok) in source_stems for tok in novel)

    def introduces_new_entities(self, turn_text: str) -> list[str]:
        """Proper-noun-like tokens absent from the source facts.

        The concrete §12.3 violation is inventing a named entity or figure —
        "Kellyanne Conway", "Sean Hannity", "the IMF". Interpretive vocabulary
        ("speculative", "mechanism", "pattern") is legitimate analysis, not a
        new fact, and cannot be pre-allowlisted without chasing the model's
        every word. So this checks only capitalized multi-word sequences and
        capitalized tokens that do not appear in the source — the bounded,
        checkable class of invention. Used by the persona floor.
        """
        text = turn_text.strip()
        if not text:
            return []
        " ".join(sorted(self.source_tokens))
        # Capitalized words not at sentence start are likely proper nouns.
        words = re.findall(r"\b[A-Z][a-zA-Z]+\b", text)
        invented = []
        for w in words:
            if w.lower() in self.source_tokens:
                continue
            # Skip sentence-initial words (capitalized by grammar, not name).
            idx = text.find(w)
            prefix = text[:idx].rstrip()
            if prefix.endswith((".", "!", "?", ";", ":")) or not prefix:
                continue
            if _stem(w.lower()) in {_stem(s) for s in self.source_tokens}:
                continue
            invented.append(w)
        return invented

    def introduces_new_numbers(self, turn_text: str) -> list[str]:
        """Numeric tokens absent from the source facts.

        A fabricated figure is invention even without a named entity — "47
        prior cases since 2019" invents both the count and the year while
        introducing no proper noun, so the entity check alone would miss it.
        """
        return sorted(_numbers(turn_text) - self.source_numbers)

    def fabrication_reason(self, turn_text: str) -> str | None:
        """Why a turn fails the grounding floor, or None if it passes.

        The bounded, checkable classes of §12.3 invention: a named entity or a
        figure not in the source. Interpretive vocabulary is not policed.
        """
        invented_entities = self.introduces_new_entities(turn_text)
        if invented_entities:
            return (f"introduced entity '{', '.join(invented_entities[:3])}' "
                    f"not in the shared facts")
        invented_numbers = self.introduces_new_numbers(turn_text)
        if invented_numbers:
            return (f"introduced figure(s) "
                    f"'{', '.join(invented_numbers[:3])}' not in the shared "
                    f"facts")
        return None


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

    def build_prompt(self, facts: list[str]) -> str:
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

    def compose(self, raw_text: str, facts: list[str]) -> EditorialPlan:
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


def _extract_json_safe(text: str) -> Any | None:
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
