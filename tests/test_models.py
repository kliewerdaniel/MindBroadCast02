"""Tests for domain models.

Rewritten for the Phase 1 model (spec §5). The old field names
(`story.title`, `event.source_url`, `claim.status`, `Correction.explanation`)
belonged to the pre-Phase-1 model where Event conflated publication artifact
with real-world occurrence and Correction was disguised as Evidence. Those
tests asserted the old shape; these assert the spec shape.
"""

from datetime import UTC, datetime

import pytest

from core.models import (
    Article,
    Claim,
    ClaimEvidence,
    ClaimFilter,
    ClaimStatus,
    ClaimType,
    Correction,
    CorrectionType,
    Entity,
    EntityStatus,
    EntityType,
    EpistemicCategory,
    Event,
    EventStatus,
    Evidence,
    EvidenceRelationship,
    ExtractionStatus,
    OwnershipGroup,
    SnapshotDiff,
    Source,
    SourceType,
    Story,
    StoryFilter,
    StoryStatus,
    WorldSnapshot,
    claim_status_from_str,
)


class TestStoryStatus:
    def test_active(self):
        assert StoryStatus.ACTIVE.value == "active"

    def test_all_members(self):
        values = {s.value for s in StoryStatus}
        assert values == {"active", "updating", "resolved", "abandoned"}


class TestClaimStatus:
    """Spec §5.6.2 — eight statuses, distinct from claim type."""

    def test_full_status_set(self):
        values = {s.value for s in ClaimStatus}
        assert values == {
            "unassessed", "supported", "corroborated", "disputed",
            "contradicted", "unresolved", "superseded", "withdrawn",
        }

    def test_legacy_names_map_to_spec_statuses(self):
        assert ClaimStatus.UNVERIFIED is ClaimStatus.UNASSESSED
        assert ClaimStatus.PARTIALLY_VERIFIED is ClaimStatus.SUPPORTED
        assert ClaimStatus.VERIFIED is ClaimStatus.CORROBORATED

    def test_legacy_string_resolution(self):
        assert claim_status_from_str("unverified") is ClaimStatus.UNASSESSED
        assert claim_status_from_str("verified") is ClaimStatus.CORROBORATED
        assert claim_status_from_str("corroborated") is ClaimStatus.CORROBORATED

    def test_unknown_status_raises(self):
        with pytest.raises(ValueError):
            claim_status_from_str("bogus")


class TestClaimType:
    """Spec §5.6.1 — type determines which validation procedures apply."""

    def test_full_type_set(self):
        values = {t.value for t in ClaimType}
        assert values == {
            "observational", "attributed", "quantitative", "causal",
            "predictive", "normative", "interpretive",
        }


class TestEventStatus:
    def test_full_status_set(self):
        values = {s.value for s in EventStatus}
        assert values == {
            "emerging", "active", "developing", "dormant",
            "concluded", "disputed", "merged", "superseded",
        }


class TestEntityType:
    def test_person(self):
        assert EntityType.PERSON.value == "person"

    def test_organization(self):
        assert EntityType.ORGANIZATION.value == "organization"

    def test_spec_types_present(self):
        values = {t.value for t in EntityType}
        assert {"person", "organization", "location", "concept",
                "country", "institution", "product", "law", "conflict"} <= values


class TestEvidenceRelationship:
    def test_full_relationship_set(self):
        values = {r.value for r in EvidenceRelationship}
        assert values == {
            "supports", "contradicts", "qualifies", "attributes",
            "provides_context", "leaves_unresolved",
        }


class TestCorrectionType:
    """Spec §5.8 — publisher correction ≠ system extraction correction."""

    def test_full_type_set(self):
        values = {t.value for t in CorrectionType}
        assert values == {
            "publisher", "system_extraction", "entity_resolution",
            "event_association", "claim_assessment", "broadcast",
            "administrative",
        }


class TestEpistemicCategory:
    def test_all_categories(self):
        categories = [
            EpistemicCategory.FACT, EpistemicCategory.CLAIM,
            EpistemicCategory.EVIDENCE, EpistemicCategory.ANALYSIS,
            EpistemicCategory.INTERPRETATION, EpistemicCategory.SPECULATION,
            EpistemicCategory.FICTION, EpistemicCategory.SATIRE,
            EpistemicCategory.SYNTHETIC, EpistemicCategory.UNKNOWN,
        ]
        assert len(categories) == 10
        values = {c.value for c in categories}
        assert {"fact", "claim", "evidence", "synthetic", "unknown"} <= values


# ── Source ───────────────────────────────────────────────────────────


class TestSource:
    def test_source_is_first_class(self):
        """A Source is an entity, not a URL string."""
        source = Source(
            name="Reuters",
            canonical_domain="reuters.com",
            source_type=SourceType.RSS,
            endpoint="https://reuters.com/feed",
        )
        assert source.name == "Reuters"
        assert source.id != ""
        assert source.enabled is True
        assert source.source_type is SourceType.RSS

    def test_trust_profile_is_not_a_reliability_score(self):
        """§5.2: trust_profile_id must not imply a universal score."""
        source = Source(name="Gov Agency", trust_profile_id="tp-1")
        assert source.trust_profile_id == "tp-1"
        # No numeric reliability field exists on Source at all.
        assert not hasattr(source, "reliability")

    def test_ownership_group_supported(self):
        group = OwnershipGroup(name="News Corp")
        source = Source(name="Outlet A", ownership_group_id=group.id)
        assert source.ownership_group_id == group.id

    def test_default_timestamps_are_utc_aware(self):
        source = Source(name="X")
        assert source.created_at.tzinfo is not None


# ── Article ──────────────────────────────────────────────────────────


class TestArticle:
    def test_article_carries_source_fk_not_url(self):
        article = Article(
            source_id="src-1",
            canonical_url="https://example.com/a",
            content_hash="abc",
        )
        assert article.source_id == "src-1"
        assert not hasattr(article, "source_url")

    def test_published_and_first_seen_are_distinct(self):
        """§5.3: these are deliberately separate."""
        published = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        article = Article(
            source_id="s", canonical_url="u", content_hash="h",
            published_at=published,
        )
        assert article.published_at == published
        assert article.first_seen_at != published

    def test_version_chain_field(self):
        article = Article(
            source_id="s", canonical_url="u", content_hash="h",
            publication_version=2, supersedes_article_id="old-id",
        )
        assert article.publication_version == 2
        assert article.supersedes_article_id == "old-id"


# ── Event ────────────────────────────────────────────────────────────


class TestEvent:
    def test_event_is_an_occurrence_not_an_article(self):
        """§5.5: Event has no source_url, content, or extractor."""
        event = Event(
            canonical_title="Vote held",
            event_type="legislative_vote",
            status=EventStatus.DEVELOPING,
        )
        assert event.status is EventStatus.DEVELOPING
        for field in ("source_url", "content", "extractor", "fetched_at"):
            assert not hasattr(event, field), f"Event must not carry {field}"

    def test_event_has_occurrence_window(self):
        start = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
        end = datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
        event = Event(canonical_title="T", occurred_at_start=start, occurred_at_end=end)
        assert event.occurred_at_start == start
        assert event.occurred_at_end == end

    def test_event_status_represents_system_view(self):
        """Status describes the system's representation, not objective reality."""
        event = Event(canonical_title="T", status=EventStatus.DISPUTED)
        assert event.status is EventStatus.DISPUTED


# ── Story ────────────────────────────────────────────────────────────


class TestStory:
    def test_default_story(self):
        story = Story(canonical_title="Test")
        assert story.canonical_title == "Test"
        assert story.status is StoryStatus.ACTIVE
        assert story.confidence == 0.0
        assert story.corrected_by is None
        assert story.id != ""

    def test_story_tracks_coverage_history(self):
        """§5.5.1: coverage tracking fields."""
        last = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        story = Story(
            canonical_title="T",
            last_broadcast_at=last,
            last_substantial_coverage_at=last,
            unresolved_question_count=3,
            audience_relevance_state="high",
        )
        assert story.last_broadcast_at == last
        assert story.unresolved_question_count == 3
        assert story.audience_relevance_state == "high"

    def test_story_with_all_fields(self):
        now = datetime.now(UTC)
        story = Story(
            id="s1", canonical_title="Test", editorial_summary="A test story",
            status=StoryStatus.RESOLVED, created_at=now,
            entity_ids=["e1", "e2"], claim_ids=["c1"],
            confidence=0.8, corrected_by="original-s1",
        )
        assert story.id == "s1"
        assert story.status is StoryStatus.RESOLVED
        assert story.entity_ids == ["e1", "e2"]
        assert story.claim_ids == ["c1"]
        assert story.confidence == 0.8
        assert story.corrected_by == "original-s1"


# ── Claim ────────────────────────────────────────────────────────────


class TestClaim:
    def test_default_claim(self):
        claim = Claim(canonical_proposition="Test claim")
        assert claim.epistemic_status is ClaimStatus.UNASSESSED
        assert claim.claim_type is ClaimType.OBSERVATIONAL
        assert claim.confidence == 0.0
        assert claim.corrected_by is None

    def test_claim_type_and_status_are_distinct(self):
        """§5.6.2: a claim's type is not its status."""
        claim = Claim(
            canonical_proposition="Output rose 5%",
            claim_type=ClaimType.QUANTITATIVE,
            epistemic_status=ClaimStatus.SUPPORTED,
        )
        assert claim.claim_type is ClaimType.QUANTITATIVE
        assert claim.epistemic_status is ClaimStatus.SUPPORTED

    def test_proposition_text_is_preserved(self):
        claim = Claim(id="c1", canonical_proposition="Original")
        assert claim.canonical_proposition == "Original"

    def test_normalized_triple_is_optional(self):
        claim = Claim(
            canonical_proposition="The committee approved the proposal",
            normalized_subject_id="ent-committee",
            normalized_predicate="approved",
            normalized_object="prop-1",
        )
        assert claim.normalized_predicate == "approved"

    def test_valid_time_window(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        claim = Claim(
            canonical_proposition="X", valid_time_start=start, valid_time_end=None
        )
        assert claim.valid_time_start == start

    def test_supersession_link(self):
        claim = Claim(canonical_proposition="X", supersedes_claim_id="old")
        assert claim.supersedes_claim_id == "old"


# ── Evidence ─────────────────────────────────────────────────────────


class TestEvidence:
    def test_default_evidence(self):
        evidence = Evidence(source_id="src-1", excerpt="passage")
        assert evidence.excerpt == "passage"
        assert evidence.provenance_status == "unverified"

    def test_evidence_is_not_an_article(self):
        evidence = Evidence(article_id="a1", source_id="s1")
        assert evidence.article_id == "a1"

    def test_evidence_carries_extraction_provenance(self):
        evidence = Evidence(
            source_id="s1",
            extraction_method="llm",
            extraction_model="qwen3:8b",
            extraction_version="v3",
        )
        assert evidence.extraction_model == "qwen3:8b"
        assert evidence.extraction_version == "v3"

    def test_evidence_has_no_correction_of_field(self):
        """Corrections are their own object; evidence never carries them."""
        assert not hasattr(Evidence(source_id="s"), "correction_of")


class TestClaimEvidence:
    def test_relationship_lives_on_the_association(self):
        link = ClaimEvidence(
            claim_id="c1", evidence_id="e1",
            relationship_type=EvidenceRelationship.SUPPORTS,
        )
        assert link.relationship_type is EvidenceRelationship.SUPPORTS

    def test_same_evidence_can_contradict_another_claim(self):
        """One document supports one proposition while contradicting another."""
        supports = ClaimEvidence(
            claim_id="c1", evidence_id="e1",
            relationship_type=EvidenceRelationship.SUPPORTS,
        )
        contradicts = ClaimEvidence(
            claim_id="c2", evidence_id="e1",
            relationship_type=EvidenceRelationship.CONTRADICTS,
        )
        assert supports.evidence_id == contradicts.evidence_id
        assert supports.relationship_type != contradicts.relationship_type


# ── Correction ───────────────────────────────────────────────────────


class TestCorrection:
    def test_correction_is_first_class(self):
        """§5.8: a Correction is not an Evidence record."""
        correction = Correction(
            correction_type=CorrectionType.PUBLISHER,
            target_type="claim", target_id="c1",
            reason="Publisher restated the figure",
        )
        assert correction.correction_type is CorrectionType.PUBLISHER
        assert correction.target_id == "c1"
        assert correction.review_status == "pending"

    def test_correction_distinguishes_publisher_from_system(self):
        publisher = Correction(
            correction_type=CorrectionType.PUBLISHER, target_type="article",
            target_id="a1",
        )
        system = Correction(
            correction_type=CorrectionType.SYSTEM_EXTRACTION,
            target_type="article", target_id="a1",
        )
        assert publisher.correction_type is not system.correction_type

    def test_correction_preserves_original_reference(self):
        correction = Correction(
            correction_type=CorrectionType.CLAIM_ASSESSMENT,
            target_type="claim", target_id="c1",
            original_value_reference="claim-v1",
            corrected_value_reference="claim-v2",
        )
        assert correction.original_value_reference == "claim-v1"
        assert correction.corrected_value_reference == "claim-v2"

    def test_correction_supersedes_correction(self):
        correction = Correction(
            correction_type=CorrectionType.PUBLISHER, target_type="claim",
            target_id="c1", supersedes_correction_id="corr-old",
        )
        assert correction.supersedes_correction_id == "corr-old"


# ── WorldSnapshot ────────────────────────────────────────────────────


class TestWorldSnapshot:
    def test_default_snapshot_is_empty_but_structured(self):
        snapshot = WorldSnapshot()
        assert snapshot.story_count == 0
        assert snapshot.claim_count == 0
        assert snapshot.evidence_count == 0
        assert snapshot.entity_count == 0
        assert snapshot.included_record_manifest == {}

    def test_snapshot_carries_watermark_and_manifest(self):
        """§5.9: not an aggregation of counts."""
        snapshot = WorldSnapshot(
            knowledge_watermark="evt-42",
            content_hash="deadbeef",
            included_record_manifest={"stories": ["s1"], "claims": ["c1"]},
            parent_snapshot_id="snap-old",
        )
        assert snapshot.knowledge_watermark == "evt-42"
        assert snapshot.content_hash == "deadbeef"
        assert snapshot.included_record_manifest["stories"] == ["s1"]
        assert snapshot.parent_snapshot_id == "snap-old"

    def test_snapshot_tracks_unresolved_issues(self):
        snapshot = WorldSnapshot(unresolved_issue_count=4)
        assert snapshot.unresolved_issue_count == 4


# ── Entity ───────────────────────────────────────────────────────────


class TestEntity:
    def test_default_entity(self):
        entity = Entity(canonical_name="Test")
        assert entity.entity_type is EntityType.CONCEPT
        assert entity.status is EntityStatus.ACTIVE

    def test_entity_with_aliases(self):
        entity = Entity(
            canonical_name="Test", entity_type=EntityType.ORGANIZATION,
            aliases=["T1", "T2"],
        )
        assert entity.entity_type is EntityType.ORGANIZATION
        assert entity.aliases == ["T1", "T2"]

    def test_entity_status_supports_merge(self):
        """§5.4: merges are explicit, reversible operations."""
        entity = Entity(canonical_name="X", status=EntityStatus.MERGED)
        assert entity.status is EntityStatus.MERGED

    def test_external_identifiers(self):
        entity = Entity(
            canonical_name="Test",
            external_identifiers={"wikidata": "Q42"},
        )
        assert entity.external_identifiers["wikidata"] == "Q42"


# ── Filters and diffs ────────────────────────────────────────────────


class TestFilters:
    def test_story_filter(self):
        f = StoryFilter(status=StoryStatus.ACTIVE)
        assert f.status is StoryStatus.ACTIVE

    def test_claim_filter_by_type(self):
        f = ClaimFilter(claim_type=ClaimType.CAUSAL)
        assert f.claim_type is ClaimType.CAUSAL

    def test_claim_filter_by_status(self):
        f = ClaimFilter(status=ClaimStatus.CONTRADICTED)
        assert f.status is ClaimStatus.CONTRADICTED


class TestSnapshotDiff:
    def test_default_diff(self):
        diff = SnapshotDiff(a_id="a1", b_id="b2")
        assert diff.new_stories == []
        assert diff.new_claims == []
        assert diff.new_articles == []
        assert diff.new_corrections == []
        assert diff.human_readable == ""


class TestExtractionStatus:
    def test_partial_is_explicit(self):
        """§8.2: partial processing must be explicitly flagged."""
        assert ExtractionStatus.PARTIAL.value == "partial"
