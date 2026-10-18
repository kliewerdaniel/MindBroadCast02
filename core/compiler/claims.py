"""Pass 3: Claim extraction.

Spec §8.2 is unambiguous about the hard requirement:

    The extraction process must preserve attribution.
    The system must not silently transform an attributed statement or
    hypothesis into an unqualified factual assertion.

Three sentences that mean different things:

    A minister said that the policy had reduced inflation.   -> ATTRIBUTED
    The policy reduced inflation.                            -> OBSERVATIONAL
    An analyst argued that the policy may reduce inflation.  -> ATTRIBUTED + PREDICTIVE

So claim *type* is derived from the sentence's grammatical structure, not from
its topic. A sentence with a reporting verb and an attributed subject is
ATTRIBUTED even when its content reads like a plain fact. Getting this wrong
would launder hearsay into verified reporting — which is invariant I2, and the
single most consequential failure mode in the whole system.

Quantities, units, and temporal qualifiers are extracted because Pass 7 needs
them: two sources reporting different unemployment rates may refer to
different months, and without the qualifier the system cannot tell a
contradiction from a definitional difference (§8.2, invariant I10).
"""

import re
from dataclasses import dataclass, field

from ..models import ClaimType
from .structural import NormalizedDocument, Sentence

# Reporting verbs that signal attribution. Ordered longest-first so
# "according to" wins over a bare "to".
REPORTING_VERBS = (
    "according to", "said", "says", "stated", "announced", "declared",
    "claimed", "argued", "asserted", "reported", "noted", "added",
    "explained", "warned", "confirmed", "told", "described", "acknowledged",
    "maintained", "insisted", "suggested", "indicated", "disclosed",
    "revealed", "admitted", "denied", "reiterated", "emphasised",
    "emphasized", "stressed", "pointed out", "made clear",
)

# Hedging that marks a prediction or hypothesis rather than an assertion.
# Matched as stems with optional inflection, so "expect", "expects", and
# "expected" all trigger — a missed inflection silently turns a prediction
# into an observational claim.
HEDGE_STEMS = (
    "may", "might", "could", "would", "expect", "anticipate", "forecast",
    "predict", "project", "believe", "think", "estimate", "likely",
    "possibly", "perhaps", "probably", "aim", "hope", "plan", "seek",
    "target", "intend", "propose",
)

# Normative framing.
NORMATIVE_MARKERS = ("should", "must", "ought to", "needs to", "has to")

_VERB_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(v) for v in REPORTING_VERBS) + r")\b",
    re.IGNORECASE)
_HEDGE_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(h) for h in HEDGE_STEMS) + r")"
    r"(?:s|ed|ing|d)?\b",
    re.IGNORECASE)
_NORMATIVE_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(n) for n in NORMATIVE_MARKERS) + r")\b",
    re.IGNORECASE)

# "X said that ..." / "according to X, ..." — the attributed subject.
_ATTRIBUTED_SUBJECT = re.compile(
    r"(?:^|[,;]\s*|\bthat\s+)"
    r"((?:[A-Z][\w.-]*(?:\s+[A-Z][\w.-]*){0,3})"
    r"|(?:the\s+)?[a-z]+(?:\s+[a-z]+){0,3}?"
    r"(?:\s+(?:minister|official|officials|bank|agency|analyst|analysts|"
    r"spokesperson|spokesman|spokeswoman|president|chair|chairperson|"
    r"director|secretary|report|study|survey|data|figures)))\s*,?\s*"
    r"(?:" + "|".join(re.escape(v) for v in REPORTING_VERBS) + r")\b",
    re.IGNORECASE)

_NUMBER = re.compile(
    r"(?P<value>-?\d[\d,]*\.?\d*)\s*"
    r"(?P<unit>%|per\s?cent|percent|percentage points?|basis points?|"
    r"million|billion|trillion|thousand|hundred|"
    r"people|persons|deaths|cases|dollars?|euros?|pounds?|yen|"
    r"tons?|tonnes?|barrels?|megawatts?|gigawatts?|kilometres?|"
    r"kilometers?|miles?|degrees?\s*(?:celsius|centigrade|fahrenheit|c|f)?)",
    re.IGNORECASE)

_TEMPORAL = re.compile(
    r"\b(?P<phrase>"
    r"(?:in|during|since|by|before|after|as of|on|for)\s+"
    r"(?:(?:the\s+)?(?:first|second|third|fourth|last|past|next|coming|"
    r"previous)\s+)?"
    r"(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december|q[1-4]|week|month|quarter|year|decade|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"(?:\s+\d{4})?"
    r"|\d{4}"
    r"|(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+\d{1,2}(?:,\s*\d{4})?"
    r")", re.IGNORECASE)

_CAUSAL = re.compile(
    r"\b(?:caused|because of|due to|as a result of|led to|resulted in|"
    r"drove|pushed|pulled|contributed to|attributed to|owing to|"
    r"stemmed from|triggered|sparked)\b", re.IGNORECASE)


@dataclass
class Quantity:
    value: str
    unit: str | None
    start: int
    end: int


@dataclass
class TemporalQualifier:
    phrase: str
    start: int
    end: int


@dataclass
class ExtractedClaim:
    """A proposition extracted from one sentence."""
    canonical_proposition: str
    claim_type: ClaimType
    source_text: str
    start: int
    end: int
    sentence_index: int
    attributed_to: str | None = None
    reporting_verb: str | None = None
    quantities: list[Quantity] = field(default_factory=list)
    temporal_qualifiers: list[TemporalQualifier] = field(default_factory=list)
    is_causal: bool = False
    extraction_method: str = "syntactic_classifier"
    extraction_version: str = "1.0.0"
    confidence: float = 0.5

    @property
    def is_attributed(self) -> bool:
        return self.claim_type is ClaimType.ATTRIBUTED


@dataclass
class ClaimExtractionResult:
    article_id: str
    claims: list[ExtractedClaim] = field(default_factory=list)
    pass_name: str = "claim_extraction"
    pass_version: str = "1.0.0"
    warnings: list[str] = field(default_factory=list)


class ClaimExtractor:
    """Pass 3. Classifies each sentence into a claim type, preserving
    attribution."""

    name = "claim_extraction"
    version = "1.0.0"

    def run(self, document: NormalizedDocument) -> ClaimExtractionResult:
        result = ClaimExtractionResult(article_id=document.article_id)
        if not document.has_content:
            result.warnings.append("no sentences to extract from")
            return result

        for sentence in document.sentences:
            claim = self._extract_from_sentence(sentence, document)
            if claim is not None:
                result.claims.append(claim)
        return result

    def _extract_from_sentence(
        self, sentence: Sentence, document: NormalizedDocument
    ) -> ExtractedClaim | None:
        text = sentence.text
        if not self._is_propositional(text):
            return None

        attributed_to, verb = self._find_attribution(text)
        quantities = self._find_quantities(text, sentence.start)
        temporals = self._find_temporals(text, sentence.start)
        is_causal = bool(_CAUSAL.search(text))
        has_hedge = bool(_HEDGE_PATTERN.search(text))

        claim_type = self._classify(
            attributed=attributed_to is not None,
            has_hedge=has_hedge,
            has_quantity=bool(quantities),
            is_causal=is_causal,
            text=text,
        )

        return ExtractedClaim(
            canonical_proposition=text.strip(),
            claim_type=claim_type,
            source_text=text,
            start=sentence.start,
            end=sentence.end,
            sentence_index=sentence.index,
            attributed_to=attributed_to,
            reporting_verb=verb,
            quantities=quantities,
            temporal_qualifiers=temporals,
            is_causal=is_causal,
            confidence=self._confidence(attributed_to is not None,
                                        bool(quantities)),
        )

    # ── classification ───────────────────────────────────

    def _classify(self, *, attributed: bool, has_hedge: bool,
                  has_quantity: bool, is_causal: bool,
                  text: str) -> ClaimType:
        """Derive the claim type from grammatical structure.

        Attribution is checked first: an attributed statement stays ATTRIBUTED
        regardless of how factual its content sounds. This is the ordering
        that prevents hearsay being laundered into verified reporting.
        """
        if attributed:
            if has_hedge:
                return ClaimType.ATTRIBUTED   # "an analyst argued it may fall"
            return ClaimType.ATTRIBUTED
        if _NORMATIVE_PATTERN.search(text) and not has_quantity:
            return ClaimType.NORMATIVE
        if has_hedge:
            return ClaimType.PREDICTIVE
        if is_causal:
            return ClaimType.CAUSAL
        if has_quantity:
            return ClaimType.QUANTITATIVE
        if self._is_interpretive(text):
            return ClaimType.INTERPRETIVE
        return ClaimType.OBSERVATIONAL

    def _is_interpretive(self, text: str) -> bool:
        markers = (
            "suggests", "indicates", "signals", "reflects", "underscores",
            "highlights", "demonstrates", "reveals", "means that",
            "points to", "raises questions", "marks a", "represents a",
        )
        return any(m in text.lower() for m in markers)

    def _is_propositional(self, text: str) -> bool:
        """Filter out headings, fragments, and non-assertions."""
        stripped = text.strip()
        if len(stripped.split()) < 4:
            return False
        if stripped.endswith("?"):
            return False
        # A bare quotation with no reporting verb is a speech act, not a
        # proposition the article asserts.
        return not (stripped.startswith('"') and not _VERB_PATTERN.search(stripped))

    # ── attribution ──────────────────────────────────────

    def _find_attribution(self, text: str) -> tuple[str | None,
                                                    str | None]:
        match = _ATTRIBUTED_SUBJECT.search(text)
        if match:
            subject = match.group(1).strip()
            verb_match = _VERB_PATTERN.search(text, match.end(1))
            return subject, verb_match.group(0) if verb_match else None
        # "according to X" with no verb immediately after.
        according = re.search(
            r"according to\s+([A-Za-z][\w.-]*(?:\s+[A-Za-z][\w.-]*){0,4})",
            text, re.IGNORECASE)
        if according:
            return according.group(1).strip(), "according to"
        return None, None

    # ── quantities and qualifiers ────────────────────────

    def _find_quantities(self, text: str, offset: int) -> list[Quantity]:
        found: list[Quantity] = []
        for match in _NUMBER.finditer(text):
            found.append(Quantity(
                value=match.group("value"),
                unit=(match.group("unit").strip()
                      if match.group("unit") else None),
                start=offset + match.start(),
                end=offset + match.end(),
            ))
        return found

    def _find_temporals(self, text: str,
                        offset: int) -> list[TemporalQualifier]:
        found: list[TemporalQualifier] = []
        for match in _TEMPORAL.finditer(text):
            found.append(TemporalQualifier(
                phrase=match.group("phrase").strip(),
                start=offset + match.start(),
                end=offset + match.end(),
            ))
        return found

    def _confidence(self, attributed: bool, has_quantity: bool) -> float:
        """Extraction confidence — explicitly not evidentiary support (§8.2)."""
        score = 0.5
        if attributed:
            score += 0.2
        if has_quantity:
            score += 0.1
        return round(min(score, 0.9), 3)
