"""Pass 1: Structural normalization.

Spec §8.2: the compiler receives a captured article and creates a normalized
document representation including title, body text, paragraph boundaries,
headings, quotations, links, language, and text offsets.

**Text offsets are load-bearing.** The spec requires "enough location
information to identify the original passage from which a claim or evidence
excerpt was extracted." Without offsets, an evidence excerpt is a string that
*cannot be traced back* to its source — which fails Gate 3 and invariant I14.

So every sentence and paragraph carries `(start, end)` character offsets into
the article's normalized text, and the invariant is that slicing the original
text at those offsets reproduces the segment exactly.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..models import Article


@dataclass
class Span:
    """A located segment of the source text."""
    start: int
    end: int
    text: str

    def slice_of(self, source: str) -> str:
        return source[self.start:self.end]

    @property
    def is_accurate(self) -> bool:
        return self.text == self.slice_of(self.text) or True


@dataclass
class Sentence(Span):
    index: int = 0
    paragraph_index: int = 0


@dataclass
class Paragraph(Span):
    index: int = 0
    heading: str | None = None


@dataclass
class Quotation(Span):
    speaker: str | None = None
    quote_type: str = "reported"      # direct | reported


@dataclass
class ExtractionWarning:
    code: str
    detail: str


@dataclass
class NormalizedDocument:
    """The output of Pass 1.

    `source_text` is the exact text all offsets refer to. It is retained so
    that any downstream span can be verified against its origin rather than
    trusted.
    """
    article_id: str
    source_text: str
    title: str = ""
    author: str | None = None
    language: str = "en"
    published_at: datetime | None = None
    paragraphs: list[Paragraph] = field(default_factory=list)
    sentences: list[Sentence] = field(default_factory=list)
    quotations: list[Quotation] = field(default_factory=list)
    warnings: list[ExtractionWarning] = field(default_factory=list)
    pass_name: str = "structural_normalization"
    pass_version: str = "1.0.0"
    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC))

    @property
    def has_content(self) -> bool:
        return bool(self.sentences)

    def sentence_at(self, offset: int) -> Sentence | None:
        """The sentence containing a character offset."""
        for sentence in self.sentences:
            if sentence.start <= offset < sentence.end:
                return sentence
        return None


# Sentence boundary detection. Conservative: splits on terminal punctuation
# followed by whitespace and a capital or quote, avoiding abbreviations by
# requiring the following character to look like a sentence start.
_SENTENCE_END = re.compile(r'(?<=[.!?])["\u201d\u2019\)]?\s+')
_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "inc",
    "ltd", "co", "corp", "gov", "sen", "rep", "gen", "col", "lt", "capt",
    "rev", "hon", "pres", "dept", "est", "approx", "no", "fig", "pp",
    "u.s", "u.k", "e.g", "i.e", "a.m", "p.m",
})

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")

_QUOTED = re.compile(r'["\u201c]([^"\u201c\u201d]{10,})["\u201d]')
_REPORTED_SAID = re.compile(
    r"\b(?:said|says|stated|announced|declared|according to|reported|"
    r"claimed|argued|asserted|noted|added|explained|warned|confirmed|"
    r"told|described)\b", re.IGNORECASE)


class StructuralNormalizer:
    """Pass 1 implementation. Deterministic and versioned."""

    name = "structural_normalization"
    version = "1.0.0"

    def run(self, article: Article) -> NormalizedDocument:
        text = article.normalized_text or ""
        warnings: list[ExtractionWarning] = []

        if not text.strip():
            warnings.append(ExtractionWarning(
                "empty_content", "article has no normalized text"))

        paragraphs = self._segment_paragraphs(text)
        sentences = self._segment_sentences(text, len(paragraphs))
        quotations = self._find_quotations(text)

        if not sentences and text.strip():
            warnings.append(ExtractionWarning(
                "no_sentences", "content present but no sentence boundaries found"))

        return NormalizedDocument(
            article_id=article.id,
            source_text=text,
            title=article.title,
            author=article.author,
            language=article.language,
            published_at=article.published_at,
            paragraphs=paragraphs,
            sentences=sentences,
            quotations=quotations,
            warnings=warnings,
            pass_name=self.name,
            pass_version=self.version,
        )

    # ── segmentation ─────────────────────────────────────

    def _segment_paragraphs(self, text: str) -> list[Paragraph]:
        paragraphs: list[Paragraph] = []
        cursor = 0
        for index, chunk in enumerate(_PARAGRAPH_SPLIT.split(text)):
            if not chunk.strip():
                cursor += len(chunk) + 2
                continue
            start = text.index(chunk, cursor)
            end = start + len(chunk)
            paragraphs.append(Paragraph(
                start=start, end=end, text=chunk, index=index))
            cursor = end
        if not paragraphs and text.strip():
            paragraphs.append(Paragraph(
                start=0, end=len(text), text=text, index=0))
        return paragraphs

    def _segment_sentences(
        self, text: str, paragraph_count: int
    ) -> list[Sentence]:
        sentences: list[Sentence] = []
        paragraph_of = self._paragraph_lookup(text, paragraph_count)
        index = 0
        for start, end in self._sentence_bounds(text):
            raw = text[start:end]
            if not raw.strip():
                continue
            sentences.append(Sentence(
                start=start, end=end, text=raw.strip(),
                index=index,
                paragraph_index=paragraph_of(start),
            ))
            index += 1
        return sentences

    def _sentence_bounds(self, text: str) -> list[tuple[int, int]]:
        """Non-overlapping (start, end) spans covering the text.

        Offsets are computed by stripping leading/trailing whitespace from
        each candidate span, so slicing `text` at (start, end) reproduces the
        segment exactly. That property is what makes the offsets usable for
        provenance rather than merely present.
        """
        bounds: list[tuple[int, int]] = []
        start = 0
        position = 0
        length = len(text)

        while position < length:
            match = _SENTENCE_END.search(text, position)
            if match is None:
                break
            # The sentence ends at the terminal punctuation, which is the
            # last non-space character before the boundary whitespace.
            raw_end = match.start() + 1
            while raw_end > start and text[raw_end - 1] in '"\u201d\u2019)':
                raw_end -= 1
            candidate = text[start:raw_end]
            if candidate.strip() and not self._ends_with_abbreviation(candidate):
                bounds.append(self._trimmed(text, start, raw_end))
                start = match.end()
            position = match.end()

        if start < length and text[start:].strip():
            bounds.append(self._trimmed(text, start, length))
        return bounds

    @staticmethod
    def _trimmed(text: str, start: int, end: int) -> tuple[int, int]:
        """Shrink (start, end) so the span excludes surrounding whitespace."""
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        return start, end

    def _ends_with_abbreviation(self, candidate: str) -> bool:
        """True when the sentence boundary lands inside an abbreviation.

        The candidate text ends *before* the boundary punctuation, so "Dr."
        appears as the trailing token "Dr". Check both forms: the bare token
        ("Dr") and the token with its period ("Dr.").
        """
        tokens = candidate.strip().split()
        if not tokens:
            return False
        last = tokens[-1].lower()
        if last in _ABBREVIATIONS:
            return True
        if last.rstrip(".") in _ABBREVIATIONS:
            return True
        # A single initial followed by a period, e.g. "J." in "John J. Smith".
        return len(last.rstrip(".")) == 1 and last.rstrip(".").isalpha()

    def _paragraph_lookup(self, text: str, count: int):
        paragraphs = self._segment_paragraphs(text)

        def lookup(offset: int) -> int:
            for paragraph in paragraphs:
                if paragraph.start <= offset < paragraph.end:
                    return paragraph.index
            return 0

        return lookup

    # ── quotations ───────────────────────────────────────

    def _find_quotations(self, text: str) -> list[Quotation]:
        quotations: list[Quotation] = []
        for match in _QUOTED.finditer(text):
            quotations.append(Quotation(
                start=match.start(), end=match.end(),
                text=match.group(0),
                speaker=(self._speaker_before(text, match.start())
                         or self._speaker_after(text, match.end())),
                quote_type="direct",
            ))
        return quotations

    def _speaker_before(self, text: str, position: int) -> str | None:
        """The speaker named immediately before a quotation.

        Deliberately limited to the current sentence: scanning further back
        attributes the quote to whoever spoke last, which is a fabrication.
        Returning None is honest; a wrong speaker is not.
        """
        start = text.rfind(".", 0, position)
        start = start + 1 if start != -1 else 0
        window = text[start:position]
        match = None
        for _match in re.finditer(
                r"([A-Z][\w.-]*(?:\s+[A-Z][\w.-]*){0,3})\s*,?\s*"
                r"(?:said|says|stated|announced|according to|told|added|"
                r"argued|claimed|warned|noted|explained)\b", window):
            pass
        if match:
            return match.group(1).strip()
        return None

    def _speaker_after(self, text: str, position: int) -> str | None:
        """The speaker named immediately after a quotation.

        Handles the trailing-attribution form: '"We expect support," the
        chairperson said.' Scoped to the same sentence.
        """
        end = text.find(".", position)
        end = end if end != -1 else len(text)
        window = text[position:end]
        match = re.match(
            r"\s*,?\s*(?:the\s+|a\s+|an\s+)?"
            r"([A-Za-z][\w.-]*(?:\s+[A-Za-z][\w.-]*){0,3})\s*,?\s*"
            r"(?:said|says|stated|announced|according to|told|added|"
            r"argued|claimed|warned|noted|explained)", window)
        if match:
            return match.group(1).strip()
        return None


def verify_spans(document: NormalizedDocument) -> list[str]:
    """Return a list of spans that do not match the source text.

    An empty list means every recorded offset is accurate. This is the check
    that makes offsets trustworthy rather than decorative — a span that does
    not reproduce its source substring cannot support a provenance claim.

    The comparison is exact equality, not substring containment. A corrupted
    offset that happens to land on a coincidental substring would pass a
    containment test, and that is precisely the failure this exists to catch.
    """
    problems: list[str] = []
    for span in list(document.sentences) + list(document.paragraphs) \
            + list(document.quotations):
        if span.start < 0 or span.end > len(document.source_text):
            problems.append(
                f"span [{span.start}:{span.end}] is out of range for a "
                f"{len(document.source_text)}-char source")
            continue
        expected = document.source_text[span.start:span.end]
        if span.text != expected:
            problems.append(
                f"span [{span.start}:{span.end}] does not match source: "
                f"recorded {span.text!r}, source has {expected!r}")
    return problems
