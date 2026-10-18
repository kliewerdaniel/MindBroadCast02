"""Multi-level deduplication.

Spec §7.5 is emphatic on two points that this module is built around:

1. The five duplication levels must **not** be conflated. Exact URL, exact
   content, near-duplicate text, syndication, and semantic duplication are
   different findings with different consequences.

2. A duplicate article must not create a new event merely because its URL
   differs — and two articles discussing the same event must not be discarded
   merely because their subject matter is similar.

So the result is a *classified candidate with confidence*, never a boolean and
never an irreversible merge.

Critically (§7.5): **a near-duplicate may contain a meaningful correction, a
new quotation, or an updated casualty figure.** Such content must not be
discarded solely because most of the article is unchanged. The default
threshold is therefore conservative — it prefers associating a candidate over
discarding it, because discarding is the destructive and unrecoverable action
(invariant I9).
"""

from dataclasses import dataclass
from enum import Enum

from .canonicalization import content_hash as hash_text
from .canonicalization import text_similarity


class DuplicateKind(Enum):
    """The five levels of spec §7.5, kept distinct."""
    EXACT_URL = "exact_url"            # same canonical URL
    EXACT_CONTENT = "exact_content"    # different URL, identical content
    SYNDICATED = "syndicated"          # same content, different source
    NEAR_DUPLICATE = "near_duplicate"  # high text similarity
    NONE = "none"


@dataclass
class DuplicateCandidate:
    """A classified deduplication finding.

    `confidence` is an assessment of the *relationship*, not a truth score
    about the article's content (invariant I1).

    `content_changed` is the explicit flag distinguishing a re-observation of
    an unchanged artifact from a material revision at the same URL. It is a
    field rather than something inferred from `reason`, because the discard /
    supersede decision is load-bearing and must not depend on prose.
    """
    kind: DuplicateKind
    existing_article_id: str | None = None
    existing_canonical_url: str | None = None
    confidence: float = 0.0
    similarity: float | None = None
    reason: str = ""
    content_changed: bool = False

    @property
    def is_duplicate(self) -> bool:
        return self.kind is not DuplicateKind.NONE

    @property
    def should_discard(self) -> bool:
        """Only an exact re-observation of an unchanged artifact is discarded.

        An EXACT_URL match with different content is a material revision and
        must not be discarded — it is the case criterion 9 exists for. A
        near-duplicate may carry a correction (§7.5), and a syndicated copy
        proves a source relationship rather than redundancy.
        """
        if self.kind is DuplicateKind.EXACT_URL:
            return not self.content_changed
        return self.kind is DuplicateKind.EXACT_CONTENT

    @property
    def should_supersede(self) -> bool:
        """A same-URL re-observation with changed content is a new version."""
        return self.kind is DuplicateKind.EXACT_URL and self.content_changed


@dataclass
class DeduplicationPolicy:
    # Conservative in the direction that matters: a lower threshold prefers
    # *associating* a candidate over discarding it. Over-detection costs a
    # stored relationship; under-detection loses a correction (§7.5, I9).
    # Calibrated against a wire report reprinted with one changed figure,
    # which scores ~0.67 with 5-word shingles — so 0.5 is the floor that
    # still separates that from genuinely unrelated reporting.
    near_duplicate_threshold: float = 0.50
    shingle_size: int = 5
    detect_syndication: bool = True

    def __post_init__(self):
        if not 0.0 <= self.near_duplicate_threshold <= 1.0:
            raise ValueError("near_duplicate_threshold must be in [0, 1]")


DEFAULT_POLICY = DeduplicationPolicy()


class Deduplicator:
    """Classifies a new observation against existing captures.

    Deliberately holds no database state: the caller supplies the existing
    articles to compare against, so the comparison itself is a pure,
    testable function and never issues queries from inside a transaction.
    """

    def __init__(self, policy: DeduplicationPolicy | None = None):
        self.policy = policy or DEFAULT_POLICY

    def classify(
        self,
        canonical_url: str,
        normalized_text: str,
        source_id: str,
        existing: list["ExistingArticle"],
        text_hash: str | None = None,
    ) -> DuplicateCandidate:
        """Run the §7.5.1 pipeline in order and return the first match.

        Order matters: an exact URL match is the strongest signal, and a
        content match is stronger than a fuzzy text comparison.
        """
        if not existing:
            return DuplicateCandidate(
                kind=DuplicateKind.NONE, reason="no existing articles")

        digest = text_hash or hash_text(normalized_text)

        # 1. Canonical URL match.
        for article in existing:
            if article.canonical_url == canonical_url:
                if article.content_hash == digest:
                    return DuplicateCandidate(
                        kind=DuplicateKind.EXACT_URL,
                        existing_article_id=article.id,
                        existing_canonical_url=article.canonical_url,
                        confidence=1.0,
                        content_changed=False,
                        reason="identical URL and content: re-observation",
                    )
                return DuplicateCandidate(
                    kind=DuplicateKind.EXACT_URL,
                    existing_article_id=article.id,
                    existing_canonical_url=article.canonical_url,
                    confidence=0.9,
                    content_changed=True,
                    reason="same URL, changed content: material revision",
                )

        # 2. Content hash match.
        for article in existing:
            if article.content_hash == digest:
                same_source = article.source_id == source_id
                kind = (DuplicateKind.EXACT_CONTENT if same_source
                        else DuplicateKind.SYNDICATED)
                return DuplicateCandidate(
                    kind=kind,
                    existing_article_id=article.id,
                    existing_canonical_url=article.canonical_url,
                    confidence=0.95,
                    reason=(
                        "identical content from the same source"
                        if same_source else
                        "identical content from a different source: syndication"
                    ),
                )

        # 3. Near-duplicate text comparison.
        best: DuplicateCandidate | None = None
        best_similarity = 0.0
        for article in existing:
            if not article.normalized_text:
                continue
            similarity = text_similarity(
                normalized_text, article.normalized_text,
                self.policy.shingle_size,
            )
            if similarity > best_similarity:
                best_similarity = similarity
                if similarity >= self.policy.near_duplicate_threshold:
                    same_source = article.source_id == source_id
                    best = DuplicateCandidate(
                        kind=(
                            DuplicateKind.NEAR_DUPLICATE if same_source
                            else DuplicateKind.SYNDICATED
                        ),
                        existing_article_id=article.id,
                        existing_canonical_url=article.canonical_url,
                        confidence=round(similarity, 4),
                        similarity=round(similarity, 4),
                        reason=(
                            f"near-duplicate text (similarity {similarity:.3f})"
                            if same_source else
                            f"syndicated copy (similarity {similarity:.3f})"
                        ),
                    )

        if best is not None:
            return best

        return DuplicateCandidate(
            kind=DuplicateKind.NONE,
            similarity=round(best_similarity, 4) if best_similarity else None,
            reason="no duplicate at any level",
        )


@dataclass
class ExistingArticle:
    """The minimum an existing capture must expose for comparison."""
    id: str
    canonical_url: str
    content_hash: str
    normalized_text: str = ""
    source_id: str = ""


def detect_syndication_group(
    articles: list[ExistingArticle],
    policy: DeduplicationPolicy | None = None,
) -> list[list[str]]:
    """Group articles that share content across sources.

    Returns groups of article IDs. Used to record source relationships so
    that N outlets repeating one wire report are not counted as N independent
    confirmations (invariant I1/I8).
    """
    policy = policy or DEFAULT_POLICY
    by_hash: dict[str, list[str]] = {}
    for article in articles:
        if article.content_hash:
            by_hash.setdefault(article.content_hash, []).append(article.id)

    groups: list[list[str]] = []
    for ids in by_hash.values():
        if len(ids) > 1:
            groups.append(sorted(ids))

    if not policy.detect_syndication:
        return groups

    # Near-identical text across different sources is also syndication.
    remaining = [a for a in articles if not a.content_hash]
    seen: set[str] = set()
    for article in remaining:
        if article.id in seen:
            continue
        group = [article.id]
        for other in remaining:
            if other.id == article.id or other.id in seen:
                continue
            if article.source_id and other.source_id == article.source_id:
                continue
            similarity = text_similarity(
                article.normalized_text, other.normalized_text,
                policy.shingle_size)
            if similarity >= policy.near_duplicate_threshold:
                group.append(other.id)
                seen.add(other.id)
        if len(group) > 1:
            seen.update(group)
            groups.append(sorted(group))
    return groups
