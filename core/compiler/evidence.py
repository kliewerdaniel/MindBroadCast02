"""Pass 4: Evidence extraction.

Spec §8.2: the compiler associates source passages and structured source
artifacts with the propositions they may support, qualify, or contradict.

    Evidence extraction should preserve the distinction between the existence
    of a statement and the truth of that statement. An official transcript may
    strongly support the claim that a statement was made. It does not
    necessarily support the underlying factual proposition contained in that
    statement.

That distinction is encoded in the *relationship type*, and this pass assigns
it structurally:

- A passage whose sentence is an ATTRIBUTED claim relates as `ATTRIBUTES` —
  it evidences that someone said something, not that the thing is true.
- A passage whose sentence is a direct assertion relates as `SUPPORTS`.

Getting this backwards is invariant I2's most likely failure: a transcript
would be recorded as supporting the very proposition it only reports.

A generated summary with no traceable underlying passage is not evidence
(§5.7), so every record carries `content_reference` pointing at the span.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..models import EvidenceRelationship
from .claims import ClaimExtractionResult, ExtractedClaim
from .structural import NormalizedDocument


@dataclass
class ExtractedEvidence:
    """A located evidentiary passage tied to one proposition."""
    excerpt: str
    start: int
    end: int
    source_span_reference: str
    relationship: EvidenceRelationship
    claim_index: int
    evidence_type: str = "passage"
    extraction_method: str = "span_association"
    extraction_version: str = "1.0.0"
    provenance_status: str = "unverified"
    rationale: str = ""


@dataclass
class EvidenceExtractionResult:
    article_id: str
    evidence: List[ExtractedEvidence] = field(default_factory=list)
    pass_name: str = "evidence_extraction"
    pass_version: str = "1.0.0"
    warnings: List[str] = field(default_factory=list)


class EvidenceExtractor:
    """Pass 4. Assigns the relationship structurally from claim type."""

    name = "evidence_extraction"
    version = "1.0.0"

    def run(
        self,
        document: NormalizedDocument,
        claims: ClaimExtractionResult,
    ) -> EvidenceExtractionResult:
        result = EvidenceExtractionResult(article_id=document.article_id)
        if not claims.claims:
            result.warnings.append("no claims to attach evidence to")
            return result

        for index, claim in enumerate(claims.claims):
            result.evidence.append(self._evidence_for(document, claim, index))
        return result

    def _evidence_for(
        self,
        document: NormalizedDocument,
        claim: ExtractedClaim,
        claim_index: int,
    ) -> ExtractedEvidence:
        if claim.is_attributed:
            # The passage evidences that the statement was made. It does not
            # evidence the proposition's content (§8.2, invariant I2).
            return ExtractedEvidence(
                excerpt=claim.source_text,
                start=claim.start,
                end=claim.end,
                source_span_reference=self._reference(document, claim),
                relationship=EvidenceRelationship.ATTRIBUTES,
                claim_index=claim_index,
                evidence_type="attributed_statement",
                rationale=(
                    "sentence is attributed; the passage evidences that the "
                    "statement was made, not that its content is true"
                ),
            )

        return ExtractedEvidence(
            excerpt=claim.source_text,
            start=claim.start,
            end=claim.end,
            source_span_reference=self._reference(document, claim),
            relationship=EvidenceRelationship.SUPPORTS,
            claim_index=claim_index,
            evidence_type="passage",
            rationale=(
                "sentence is a direct assertion; the passage is the article's "
                "own support for the proposition"
            ),
        )

    @staticmethod
    def _reference(document: NormalizedDocument, claim: ExtractedClaim) -> str:
        """A stable pointer back to the source location.

        The article id plus character offsets is what makes the excerpt
        traceable; the sentence index is a human-readable hint.
        """
        return (f"{document.article_id}:{claim.start}-{claim.end}"
                f":s{claim.sentence_index}")
