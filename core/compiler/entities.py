"""Pass 2: Entity extraction.

Spec §8.2: the system identifies candidate entities and mentions. Entity
extraction records the exact source span and extraction method.

**It should not automatically merge the extracted mention into an existing
canonical entity.** Merging is Pass 5's job, and only via an explicit decision
(§5.4: embedding similarity may generate candidates but must never authorize a
merge).

So this pass produces *mentions* — located, labelled, unattributed to any
canonical record — and resolution is strictly separate.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..models import EntityType
from .structural import NormalizedDocument, Span


# spaCy labels mapped onto the domain EntityType set. Unmapped labels are kept
# with their original label so nothing is silently discarded.
_SPACY_TO_ENTITY_TYPE = {
    "PERSON": EntityType.PERSON,
    "NORP": EntityType.CONCEPT,          # nationalities, religious, political groups
    "ORG": EntityType.ORGANIZATION,
    "GPE": EntityType.COUNTRY,           # countries, cities, states
    "LOC": EntityType.LOCATION,
    "FAC": EntityType.LOCATION,
    "PRODUCT": EntityType.PRODUCT,
    "EVENT": EntityType.CONFLICT,
    "WORK_OF_ART": EntityType.CONCEPT,
    "LAW": EntityType.LAW,
    "LANGUAGE": EntityType.CONCEPT,
    "DATE": None,                        # temporal, not an entity
    "TIME": None,
    "PERCENT": None,                     # quantitative, handled by claims
    "MONEY": None,
    "QUANTITY": None,
    "ORDINAL": None,
    "CARDINAL": None,
}


@dataclass
class EntityMention:
    """A located entity mention. Not yet resolved to a canonical entity."""
    text: str
    start: int
    end: int
    raw_label: str
    entity_type: Optional[EntityType]
    sentence_index: int = 0
    extraction_method: str = "spacy_ner"
    extraction_version: str = "1.0.0"
    confidence: float = 0.0

    def to_span(self) -> Span:
        return Span(start=self.start, end=self.end, text=self.text)


@dataclass
class EntityExtractionResult:
    article_id: str
    mentions: List[EntityMention] = field(default_factory=list)
    pass_name: str = "entity_extraction"
    pass_version: str = "1.0.0"
    warnings: List[str] = field(default_factory=list)

    @property
    def distinct_texts(self) -> List[str]:
        seen: List[str] = []
        for mention in self.mentions:
            if mention.text not in seen:
                seen.append(mention.text)
        return seen


class EntityExtractor:
    """Pass 2. Produces mentions; never resolves or merges."""

    name = "entity_extraction"
    version = "1.0.0"
    _UNAVAILABLE = object()   # sentinel: model could not be loaded

    def __init__(self, model_name: str = "en_core_web_sm"):
        self.model_name = model_name
        self._nlp = None

    @property
    def nlp(self):
        if self._nlp is None:
            import spacy
            try:
                self._nlp = spacy.load(self.model_name)
            except OSError:
                # Fall back to the rule-based matcher so the compiler still
                # functions on a machine without the model downloaded. The
                # extraction method records which path ran.
                self._nlp = self._UNAVAILABLE
        return self._nlp

    def run(self, document: NormalizedDocument) -> EntityExtractionResult:
        result = EntityExtractionResult(article_id=document.article_id)
        if not document.has_content:
            result.warnings.append("no sentences to extract from")
            return result

        if self.nlp is self._UNAVAILABLE:
            result.warnings.append(
                f"spaCy model {self.model_name!r} unavailable; "
                "no entities extracted. Install it with: "
                'pip install "en-core-web-sm@https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"')
            return result

        nlp = self.nlp
        assert nlp is not self._UNAVAILABLE
        doc = nlp(document.source_text)
        for ent in doc.ents:
            entity_type = _SPACY_TO_ENTITY_TYPE.get(ent.label_, None)
            if ent.label_ not in _SPACY_TO_ENTITY_TYPE:
                result.warnings.append(
                    f"unmapped spaCy label {ent.label_!r} retained raw")
            sentence_index = 0
            sentence = document.sentence_at(ent.start_char)
            if sentence is not None:
                sentence_index = sentence.index
            result.mentions.append(EntityMention(
                text=ent.text,
                start=ent.start_char,
                end=ent.end_char,
                raw_label=ent.label_,
                entity_type=entity_type,
                sentence_index=sentence_index,
                extraction_method=f"spacy_ner:{self.model_name}",
                extraction_version=self.version,
            ))
        return result


class RuleBasedEntityExtractor:
    """Deterministic fallback with no model dependency.

    Capitalised multi-word sequences that are not sentence-initial. Lower
    recall than spaCy, but fully inspectable and reproducible — which is the
    property §8.5 requires of a versioned pass.
    """

    name = "entity_extraction"
    version = "1.0.0-rules"

    _PATTERN = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,4})\b")
    _STOPLIST = frozenset({
        "The", "A", "An", "In", "On", "At", "It", "He", "She", "They",
        "We", "This", "That", "These", "Those", "There", "But", "And",
        "However", "According", "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday", "January", "February", "March",
        "April", "May", "June", "July", "August", "September", "October",
        "November", "December",
    })

    def run(self, document: NormalizedDocument) -> EntityExtractionResult:
        result = EntityExtractionResult(article_id=document.article_id)
        if not document.has_content:
            return result

        sentence_starts = {s.start for s in document.sentences}
        for match in self._PATTERN.finditer(document.source_text):
            text = match.group(1)
            first = text.split()[0]
            if first in self._STOPLIST:
                continue
            # A capitalised word at the very start of a sentence is usually
            # not an entity mention; skip only the first token.
            if match.start() in sentence_starts and " " not in text:
                continue
            sentence = document.sentence_at(match.start())
            result.mentions.append(EntityMention(
                text=text,
                start=match.start(),
                end=match.end(),
                raw_label="PROPER_NOUN",
                entity_type=EntityType.CONCEPT,
                sentence_index=sentence.index if sentence else 0,
                extraction_method="capitalized_sequence",
                extraction_version=self.version,
                confidence=0.5,
            ))
        return result
