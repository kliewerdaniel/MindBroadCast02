"""Persistence layer tests.

Covers the Gate 1 requirement: the system reliably persists and retrieves its
core domain records without violating the defined relationships.
"""

import asyncio
import os
from datetime import UTC, datetime

import pytest

from core.models import (
    Article,
    Claim,
    ClaimFilter,
    ClaimStatus,
    ClaimType,
    Correction,
    CorrectionType,
    Entity,
    EntityStatus,
    EntityType,
    Event,
    EventStatus,
    Evidence,
    EvidenceRelationship,
    OwnershipGroup,
    Source,
    SourceType,
    Story,
    StoryFilter,
    StoryStatus,
)
from core.persistence import (
    SCHEMA_VERSION,
    Database,
    PersistenceError,
    WorldStore,
    content_hash,
)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Database ─────────────────────────────────────────────────────────


class TestDatabase:
    def test_initialize_creates_tables(self, db_path):
        db = Database(db_path)
        try:
            version = db.initialize()
            assert version == SCHEMA_VERSION
        finally:
            db.close()

    def test_get_schema_version(self, db_path):
        db = Database(db_path)
        try:
            db.initialize()
            assert db.get_schema_version() == SCHEMA_VERSION
        finally:
            db.close()

    def test_foreign_keys_enabled(self, world_store):
        conn = world_store.db.connect()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    def test_wal_mode_on_disk(self, db_path):
        db = Database(db_path)
        try:
            db.initialize()
            mode = db.connect().execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            db.close()

    def test_in_memory_databases_are_isolated(self):
        """Two stores must never share an in-memory database."""
        a = WorldStore(":memory:")
        b = WorldStore(":memory:")
        try:
            story = Story(canonical_title="Isolated")
            run(a.add_story(story))
            assert run(b.get_story(story.id)) is None
        finally:
            a.close()
            b.close()

    def test_busy_timeout_configured(self, world_store):
        conn = world_store.db.connect()
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout >= 1000

    def test_cross_instance_on_disk_persistence(self, db_path):
        store = WorldStore(db_path)
        story = Story(canonical_title="Persisted")
        story_id = run(store.add_story(story))
        store.close()

        reopened = WorldStore(db_path)
        try:
            retrieved = run(reopened.get_story(story_id))
            assert retrieved is not None
            assert retrieved.canonical_title == "Persisted"
        finally:
            reopened.close()


# ── Sources ──────────────────────────────────────────────────────────


class TestSourcePersistence:
    def test_add_and_get_source(self, world_store, sample_source):
        source_id = run(world_store.add_source(sample_source))
        retrieved = run(world_store.get_source(source_id))
        assert retrieved is not None
        assert retrieved.name == "Reuters"
        assert retrieved.source_type is SourceType.RSS
        assert retrieved.enabled is True

    def test_list_sources(self, world_store, sample_source):
        run(world_store.add_source(sample_source))
        run(world_store.add_source(Source(name="AP", endpoint="https://ap.org/feed")))
        sources = run(world_store.list_sources())
        assert len(sources) == 2

    def test_list_enabled_only(self, world_store, sample_source):
        run(world_store.add_source(sample_source))
        disabled = Source(name="Off", endpoint="x", enabled=False)
        run(world_store.add_source(disabled))
        assert len(run(world_store.list_sources(enabled_only=True))) == 1

    def test_ownership_group_relationship(self, world_store, sample_source):
        group = OwnershipGroup(name="News Corp")
        run(world_store.add_ownership_group(group))
        sample_source.ownership_group_id = group.id
        run(world_store.add_source(sample_source))
        retrieved = run(world_store.get_source(sample_source.id))
        assert retrieved.ownership_group_id == group.id

    def test_orphan_ownership_group_rejected(self, world_store, sample_source):
        sample_source.ownership_group_id = "nonexistent"
        with pytest.raises(PersistenceError):
            run(world_store.add_source(sample_source))

    def test_source_attempt_tracking(self, world_store, sample_source):
        source_id = run(world_store.add_source(sample_source))
        run(world_store.record_source_attempt(source_id, success=False))
        run(world_store.record_source_attempt(source_id, success=False))
        retrieved = run(world_store.get_source(source_id))
        assert retrieved.failure_count == 2
        assert retrieved.last_attempt_at is not None

    def test_success_resets_failure_count(self, world_store, sample_source):
        source_id = run(world_store.add_source(sample_source))
        run(world_store.record_source_attempt(source_id, success=False))
        run(world_store.record_source_attempt(source_id, success=True))
        retrieved = run(world_store.get_source(source_id))
        assert retrieved.failure_count == 0
        assert retrieved.last_success_at is not None


# ── Articles ─────────────────────────────────────────────────────────


class TestArticlePersistence:
    def test_add_and_get_article(self, world_store, sample_source, sample_article):
        source_id = run(world_store.add_source(sample_source))
        sample_article.source_id = source_id
        article_id = run(world_store.add_article(sample_article))
        retrieved = run(world_store.get_article(article_id))
        assert retrieved is not None
        assert retrieved.title == "Climate Summit Begins"
        assert retrieved.source_id == source_id

    def test_article_requires_valid_source(self, world_store, sample_article):
        """An article with an orphan source_id must be rejected."""
        with pytest.raises(PersistenceError):
            run(world_store.add_article(sample_article))

    def test_published_and_first_seen_remain_distinct(self, world_store,
                                                      sample_source, sample_article):
        run(world_store.add_source(sample_source))
        sample_article.source_id = sample_source.id
        article_id = run(world_store.add_article(sample_article))
        retrieved = run(world_store.get_article(article_id))
        assert retrieved.published_at is not None
        assert retrieved.first_seen_at != retrieved.published_at

    def test_duplicate_content_hash_rejected(self, world_store,
                                             sample_source, sample_article):
        run(world_store.add_source(sample_source))
        sample_article.source_id = sample_source.id
        run(world_store.add_article(sample_article))
        with pytest.raises(PersistenceError):
            run(world_store.add_article(sample_article))

    def test_supersede_preserves_original(self, world_store,
                                          sample_source, sample_article):
        """§5.3.1: a material revision must not erase the historical version."""
        run(world_store.add_source(sample_source))
        sample_article.source_id = sample_source.id
        original_id = run(world_store.add_article(sample_article))

        revised = Article(
            source_id=sample_source.id,
            canonical_url=sample_article.canonical_url,
            title="Climate Summit Begins (corrected)",
            content_hash="c" * 64,
            normalized_text="Corrected: leaders met to discuss climate policy.",
            published_at=datetime(2026, 1, 2, 8, 0, tzinfo=UTC),
        )
        new_id = run(world_store.supersede_article(original_id, revised))

        original = run(world_store.get_article(original_id))
        assert original is not None, "original version must survive"
        assert original.title == "Climate Summit Begins"

        successor = run(world_store.get_article(new_id))
        assert successor.publication_version == 2
        assert successor.supersedes_article_id == original_id

    def test_article_version_chain(self, world_store, sample_source, sample_article):
        run(world_store.add_source(sample_source))
        sample_article.source_id = sample_source.id
        v1 = run(world_store.add_article(sample_article))
        v2 = run(world_store.supersede_article(v1, Article(
            source_id=sample_source.id,
            canonical_url=sample_article.canonical_url,
            title="v2", content_hash="d" * 64,
        )))
        chain = run(world_store.get_article_versions(v2))
        assert len(chain) == 2
        assert chain[0].publication_version == 1
        assert chain[1].publication_version == 2

    def test_lookup_by_url(self, world_store, sample_source, sample_article):
        run(world_store.add_source(sample_source))
        sample_article.source_id = sample_source.id
        run(world_store.add_article(sample_article))
        found = run(world_store.get_article_by_url(sample_article.canonical_url))
        assert found is not None
        assert found.id == sample_article.id


# ── Entities ─────────────────────────────────────────────────────────


class TestEntityPersistence:
    def test_add_and_get_entity(self, world_store, sample_entity):
        entity_id = run(world_store.add_entity(sample_entity))
        retrieved = run(world_store.get_entity(entity_id))
        assert retrieved.canonical_name == "Climate Summit 2025"
        assert retrieved.entity_type is EntityType.CONCEPT

    def test_same_name_and_type_deduplicated(self, world_store):
        e1 = Entity(canonical_name="Test Org", entity_type=EntityType.ORGANIZATION)
        e2 = Entity(canonical_name="Test Org", entity_type=EntityType.ORGANIZATION)
        id1 = run(world_store.add_entity(e1))
        id2 = run(world_store.add_entity(e2))
        assert id1 == id2
        assert len(run(world_store.list_entities())) == 1

    def test_different_names_not_deduplicated(self, world_store):
        run(world_store.add_entity(
            Entity(canonical_name="Org 1", entity_type=EntityType.ORGANIZATION)))
        run(world_store.add_entity(
            Entity(canonical_name="Org 2", entity_type=EntityType.ORGANIZATION)))
        orgs = [
            e for e in run(world_store.list_entities())
            if e.entity_type is EntityType.ORGANIZATION
        ]
        assert len(orgs) == 2

    def test_reobservation_updates_window(self, world_store, sample_entity):
        run(world_store.add_entity(sample_entity))
        again = Entity(
            canonical_name=sample_entity.canonical_name,
            entity_type=sample_entity.entity_type,
        )
        run(world_store.add_entity(again))
        stored = run(world_store.find_entity_by_name(
            sample_entity.canonical_name, sample_entity.entity_type))
        assert stored.id == sample_entity.id

    def test_merge_is_explicit_and_reversible(self, world_store):
        survivor = Entity(canonical_name="Canonical", entity_type=EntityType.PERSON)
        absorbed = Entity(canonical_name="Alias", entity_type=EntityType.PERSON)
        s_id = run(world_store.add_entity(survivor))
        a_id = run(world_store.add_entity(absorbed))

        run(world_store.merge_entities(s_id, a_id))

        assert run(world_store.get_entity(a_id)).status is EntityStatus.MERGED
        assert run(world_store.get_entity(s_id)).status is EntityStatus.ACTIVE
        # The absorbed record still exists — the merge is reversible.
        assert run(world_store.get_entity(a_id)) is not None

    def test_merge_requires_both_to_exist(self, world_store, sample_entity):
        s_id = run(world_store.add_entity(sample_entity))
        with pytest.raises(PersistenceError):
            run(world_store.merge_entities(s_id, "nonexistent"))


# ── Events ───────────────────────────────────────────────────────────


class TestEventPersistence:
    def test_add_and_get_event(self, world_store, sample_event):
        event_id = run(world_store.add_event(sample_event))
        retrieved = run(world_store.get_event(event_id))
        assert retrieved.canonical_title == "Climate Summit Begins"
        assert retrieved.status is EventStatus.DEVELOPING

    def test_event_has_no_source_url(self, world_store, sample_event):
        """Event is an occurrence, not an article artifact."""
        event_id = run(world_store.add_event(sample_event))
        retrieved = run(world_store.get_event(event_id))
        assert not hasattr(retrieved, "source_url")

    def test_event_parent_relationship(self, world_store):
        parent = Event(canonical_title="War", event_type="conflict")
        parent_id = run(world_store.add_event(parent))
        child = Event(
            canonical_title="Battle", event_type="engagement",
            parent_event_id=parent_id,
        )
        child_id = run(world_store.add_event(child))
        assert run(world_store.get_event(child_id)).parent_event_id == parent_id

    def test_orphan_parent_rejected(self, world_store, sample_event):
        sample_event.parent_event_id = "nonexistent"
        with pytest.raises(PersistenceError):
            run(world_store.add_event(sample_event))

    def test_list_events_by_status(self, world_store, sample_event):
        run(world_store.add_event(sample_event))
        run(world_store.add_event(Event(
            canonical_title="Concluded thing", event_type="vote",
            status=EventStatus.CONCLUDED)))
        developing = run(world_store.list_events(status=EventStatus.DEVELOPING))
        assert len(developing) == 1


# ── Stories ──────────────────────────────────────────────────────────


class TestStoryPersistence:
    def test_add_and_get_story(self, world_store, sample_story):
        story_id = run(world_store.add_story(sample_story))
        retrieved = run(world_store.get_story(story_id))
        assert retrieved.canonical_title == "Test Story: Climate Summit"
        assert retrieved.status is StoryStatus.ACTIVE

    def test_list_stories(self, world_store):
        run(world_store.add_story(Story(canonical_title="Story 1")))
        run(world_store.add_story(Story(canonical_title="Story 2")))
        assert len(run(world_store.list_stories())) == 2

    def test_list_stories_with_filter(self, world_store):
        run(world_store.add_story(
            Story(canonical_title="Story 1", status=StoryStatus.ACTIVE)))
        run(world_store.add_story(
            Story(canonical_title="Story 2", status=StoryStatus.RESOLVED)))
        active = run(world_store.list_stories(
            StoryFilter(status=StoryStatus.ACTIVE)))
        assert len(active) == 1
        assert active[0].canonical_title == "Story 1"

    def test_status_change_creates_new_record(self, world_store, sample_story):
        """Append-only: the original is never modified."""
        story_id = run(world_store.add_story(sample_story))
        new_id = run(world_store.update_story_status(story_id, StoryStatus.RESOLVED))

        original = run(world_store.get_story(story_id))
        assert original.status is StoryStatus.ACTIVE, "original must not change"

        successor = run(world_store.get_story(new_id))
        assert successor.status is StoryStatus.RESOLVED
        assert successor.corrected_by == story_id

    def test_status_change_emits_event(self, world_store, sample_story):
        story_id = run(world_store.add_story(sample_story))
        run(world_store.update_story_status(story_id, StoryStatus.RESOLVED))
        events = run(world_store.list_domain_events(event_type="StoryStatusChanged"))
        assert len(events) == 1
        assert events[0]["payload"]["to_status"] == "resolved"

    def test_update_nonexistent_story_raises(self, world_store):
        with pytest.raises(PersistenceError):
            run(world_store.update_story_status("nope", StoryStatus.RESOLVED))


# ── Claims ───────────────────────────────────────────────────────────


class TestClaimPersistence:
    def test_add_and_get_claim(self, world_store, sample_claim):
        claim_id = run(world_store.add_claim(sample_claim))
        retrieved = run(world_store.get_claim(claim_id))
        assert retrieved.canonical_proposition == sample_claim.canonical_proposition
        assert retrieved.claim_type is ClaimType.QUANTITATIVE

    def test_list_claims(self, world_store):
        run(world_store.add_claim(Claim(canonical_proposition="Claim 1")))
        run(world_store.add_claim(Claim(canonical_proposition="Claim 2")))
        assert len(run(world_store.list_claims())) == 2

    def test_list_claims_by_status(self, world_store):
        run(world_store.add_claim(Claim(
            canonical_proposition="C1", epistemic_status=ClaimStatus.UNASSESSED)))
        run(world_store.add_claim(Claim(
            canonical_proposition="C2", epistemic_status=ClaimStatus.CORROBORATED)))
        corroborated = run(world_store.list_claims(
            ClaimFilter(status=ClaimStatus.CORROBORATED)))
        assert len(corroborated) == 1

    def test_list_claims_by_type(self, world_store):
        run(world_store.add_claim(Claim(
            canonical_proposition="X caused Y", claim_type=ClaimType.CAUSAL)))
        run(world_store.add_claim(Claim(
            canonical_proposition="Z happened", claim_type=ClaimType.OBSERVATIONAL)))
        causal = run(world_store.list_claims(
            ClaimFilter(claim_type=ClaimType.CAUSAL)))
        assert len(causal) == 1

    def test_status_transition_preserves_prior_assessment(self, world_store,
                                                          sample_claim):
        claim_id = run(world_store.add_claim(sample_claim))
        run(world_store.set_claim_status(
            claim_id, ClaimStatus.CORROBORATED, reason="independent source"))
        original = run(world_store.get_claim(claim_id))
        assert original.epistemic_status is ClaimStatus.UNASSESSED
        claims = run(world_store.list_claims())
        assert len(claims) == 2, "the new assessment is a separate record"

    def test_status_transition_emits_event(self, world_store, sample_claim):
        claim_id = run(world_store.add_claim(sample_claim))
        run(world_store.set_claim_status(claim_id, ClaimStatus.DISPUTED, reason="conflict"))
        events = run(world_store.list_domain_events(
            event_type="ClaimAssessmentChanged"))
        assert len(events) == 1
        assert events[0]["payload"]["from_status"] == "unassessed"
        assert events[0]["payload"]["to_status"] == "disputed"


# ── Evidence and associations ────────────────────────────────────────


class TestEvidencePersistence:
    def test_add_and_get_evidence(self, world_store, sample_source,
                                  sample_article, sample_evidence):
        run(world_store.add_source(sample_source))
        sample_article.source_id = sample_source.id
        run(world_store.add_article(sample_article))
        sample_evidence.article_id = sample_article.id
        sample_evidence.source_id = sample_source.id
        evidence_id = run(world_store.add_evidence(sample_evidence))
        retrieved = run(world_store.get_evidence(evidence_id))
        assert retrieved.excerpt == sample_evidence.excerpt
        assert retrieved.extraction_model == "qwen3:8b"

    def test_evidence_requires_valid_article(self, world_store, sample_evidence):
        sample_evidence.article_id = "nonexistent"
        with pytest.raises(PersistenceError):
            run(world_store.add_evidence(sample_evidence))

    def test_link_evidence_to_claim(self, world_store, sample_source,
                                    sample_article, sample_evidence, sample_claim):
        run(world_store.add_source(sample_source))
        sample_article.source_id = sample_source.id
        run(world_store.add_article(sample_article))
        sample_evidence.article_id = sample_article.id
        sample_evidence.source_id = sample_source.id
        evidence_id = run(world_store.add_evidence(sample_evidence))
        claim_id = run(world_store.add_claim(sample_claim))

        run(world_store.link_evidence(
            claim_id, evidence_id, EvidenceRelationship.SUPPORTS,
            assessment_method="deterministic"))
        evidence = run(world_store.get_evidence_for_claim(claim_id))
        assert len(evidence) == 1
        assert evidence[0].id == evidence_id

    def test_same_evidence_supports_one_claim_contradicts_another(
            self, world_store, sample_source, sample_article, sample_evidence):
        """One document can support one proposition while contradicting another."""
        run(world_store.add_source(sample_source))
        sample_article.source_id = sample_source.id
        run(world_store.add_article(sample_article))
        sample_evidence.article_id = sample_article.id
        sample_evidence.source_id = sample_source.id
        evidence_id = run(world_store.add_evidence(sample_evidence))

        c1 = run(world_store.add_claim(Claim(canonical_proposition="P1")))
        c2 = run(world_store.add_claim(Claim(canonical_proposition="P2")))

        run(world_store.link_evidence(c1, evidence_id, EvidenceRelationship.SUPPORTS))
        run(world_store.link_evidence(c1, evidence_id, EvidenceRelationship.CONTRADICTS))
        run(world_store.link_evidence(c2, evidence_id, EvidenceRelationship.CONTRADICTS))

        supporting = run(world_store.get_evidence_for_claim(
            c1, EvidenceRelationship.SUPPORTS))
        contradicting = run(world_store.get_evidence_for_claim(
            c1, EvidenceRelationship.CONTRADICTS))
        assert len(supporting) == 1
        assert len(contradicting) == 1

    def test_duplicate_link_rejected(self, world_store, sample_source,
                                     sample_article, sample_evidence, sample_claim):
        run(world_store.add_source(sample_source))
        sample_article.source_id = sample_source.id
        run(world_store.add_article(sample_article))
        sample_evidence.article_id = sample_article.id
        sample_evidence.source_id = sample_source.id
        evidence_id = run(world_store.add_evidence(sample_evidence))
        claim_id = run(world_store.add_claim(sample_claim))
        run(world_store.link_evidence(claim_id, evidence_id,
                                      EvidenceRelationship.SUPPORTS))
        with pytest.raises(PersistenceError):
            run(world_store.link_evidence(claim_id, evidence_id,
                                          EvidenceRelationship.SUPPORTS))

    def test_link_requires_valid_ids(self, world_store, sample_claim):
        claim_id = run(world_store.add_claim(sample_claim))
        with pytest.raises(PersistenceError):
            run(world_store.link_evidence(claim_id, "nonexistent",
                                          EvidenceRelationship.SUPPORTS))


# ── Corrections ──────────────────────────────────────────────────────


class TestCorrectionPersistence:
    def test_correction_is_not_evidence(self, world_store, sample_claim,
                                        sample_correction):
        """§5.8: corrections must never be stored as Evidence rows."""
        claim_id = run(world_store.add_claim(sample_claim))
        sample_correction.target_id = claim_id
        correction_id = run(world_store.add_correction(sample_correction))

        assert run(world_store.count_evidence()) == 0, \
            "a correction must not create an evidence record"
        assert run(world_store.count_corrections()) == 1
        retrieved = run(world_store.get_correction(correction_id))
        assert retrieved.correction_type is CorrectionType.PUBLISHER

    def test_correction_preserves_target(self, world_store, sample_claim,
                                         sample_correction):
        claim_id = run(world_store.add_claim(sample_claim))
        sample_correction.target_id = claim_id
        run(world_store.add_correction(sample_correction))
        original = run(world_store.get_claim(claim_id))
        assert original.canonical_proposition == sample_claim.canonical_proposition

    def test_corrections_for_target(self, world_store, sample_correction):
        run(world_store.add_correction(sample_correction))
        second = Correction(
            correction_type=CorrectionType.SYSTEM_EXTRACTION,
            target_type="claim", target_id="claim-1",
            reason="extraction was wrong",
        )
        run(world_store.add_correction(second))
        found = run(world_store.get_corrections_for("claim", "claim-1"))
        assert len(found) == 2
        types = {c.correction_type for c in found}
        assert CorrectionType.PUBLISHER in types
        assert CorrectionType.SYSTEM_EXTRACTION in types

    def test_correction_emits_event(self, world_store, sample_correction):
        run(world_store.add_correction(sample_correction))
        events = run(world_store.list_domain_events(
            event_type="CorrectionDetected"))
        assert len(events) == 1
        assert events[0]["payload"]["correction_type"] == "publisher"

    def test_correction_supersedes_correction(self, world_store, sample_correction):
        first_id = run(world_store.add_correction(sample_correction))
        second = Correction(
            correction_type=CorrectionType.PUBLISHER,
            target_type="claim", target_id="claim-1",
            supersedes_correction_id=first_id,
        )
        run(world_store.add_correction(second))
        retrieved = run(world_store.get_correction(first_id))
        assert retrieved is not None, "superseded correction is never deleted"


# ── Append-only ──────────────────────────────────────────────────────


class TestAppendOnly:
    def test_historical_records_survive_status_change(self, world_store,
                                                      sample_story):
        story_id = run(world_store.add_story(sample_story))
        run(world_store.update_story_status(story_id, StoryStatus.RESOLVED))
        assert run(world_store.get_story(story_id)) is not None

    def test_no_update_or_delete_statements_in_write_paths(self):
        """Static guard: the schema-level write paths never mutate core rows."""
        import inspect

        from core import persistence
        source = inspect.getsource(persistence.WorldStore)
        for method in ("add_claim", "add_story", "add_article", "add_correction"):
            body = source.split(f"async def {method}")[1].split("async def")[0]
            assert "DELETE FROM" not in body
            assert "UPDATE" not in body or method == "update_story_status"


# ── Snapshots ────────────────────────────────────────────────────────


class TestSnapshotPersistence:
    def test_empty_snapshot(self, world_store):
        snapshot = run(world_store.create_snapshot())
        assert snapshot.story_count == 0
        assert snapshot.claim_count == 0
        assert snapshot.evidence_count == 0
        assert snapshot.entity_count == 0
        assert snapshot.article_count == 0

    def test_snapshot_records_full_state(self, world_store, sample_story,
                                         sample_claim, sample_entity):
        run(world_store.add_story(sample_story))
        run(world_store.add_claim(sample_claim))
        run(world_store.add_entity(sample_entity))
        snapshot = run(world_store.create_snapshot())
        assert snapshot.story_count == 1
        assert snapshot.claim_count == 1
        assert snapshot.entity_count == 1

    def test_snapshot_has_watermark_hash_and_manifest(self, world_store,
                                                      sample_story):
        run(world_store.add_story(sample_story))
        snapshot = run(world_store.create_snapshot())
        assert snapshot.knowledge_watermark != ""
        assert snapshot.content_hash != ""
        assert "stories" in snapshot.included_record_manifest
        assert sample_story.id in snapshot.included_record_manifest["stories"]

    def test_snapshot_has_unique_ids(self, world_store):
        s1 = run(world_store.create_snapshot())
        s2 = run(world_store.create_snapshot())
        assert s1.id != s2.id

    def test_snapshot_parent_chain(self, world_store):
        s1 = run(world_store.create_snapshot())
        s2 = run(world_store.create_snapshot())
        assert s1.parent_snapshot_id is None
        assert s2.parent_snapshot_id == s1.id

    def test_snapshot_persists_and_retrieves(self, world_store, sample_story):
        run(world_store.add_story(sample_story))
        snapshot = run(world_store.create_snapshot())
        retrieved = run(world_store.get_snapshot(snapshot.id))
        assert retrieved is not None
        assert retrieved.content_hash == snapshot.content_hash
        assert retrieved.included_record_manifest == snapshot.included_record_manifest

    def test_list_snapshots(self, world_store):
        for _ in range(3):
            run(world_store.create_snapshot())
        assert len(run(world_store.list_snapshots())) == 3

    def test_compare_snapshots(self, world_store, sample_story):
        a = run(world_store.create_snapshot())
        run(world_store.add_story(sample_story))
        b = run(world_store.create_snapshot())
        diff = run(world_store.compare_snapshots(a.id, b.id))
        assert sample_story.id in diff.new_stories
        assert "Stories" in diff.human_readable

    def test_compare_nonexistent_raises(self, world_store):
        a = run(world_store.create_snapshot())
        with pytest.raises(PersistenceError):
            run(world_store.compare_snapshots(a.id, "nonexistent"))

    def test_snapshot_writes_artifact(self, world_store, sample_story, tmp_path):
        run(world_store.add_story(sample_story))
        snapshot = run(world_store.create_snapshot(
            artifact_dir=str(tmp_path / "snapshots")))
        assert snapshot.state_reference is not None
        assert os.path.exists(snapshot.state_reference)

    def test_snapshot_counts_only_active_entities(self, world_store):
        survivor = Entity(canonical_name="Keep", entity_type=EntityType.PERSON)
        absorbed = Entity(canonical_name="Gone", entity_type=EntityType.PERSON)
        s_id = run(world_store.add_entity(survivor))
        a_id = run(world_store.add_entity(absorbed))
        run(world_store.merge_entities(s_id, a_id))
        snapshot = run(world_store.create_snapshot())
        assert snapshot.entity_count == 1
        assert a_id not in snapshot.included_record_manifest["entities"]


# ── Domain events ────────────────────────────────────────────────────


class TestDomainEvents:
    def test_every_write_emits_an_event(self, world_store, sample_story):
        run(world_store.add_story(sample_story))
        events = run(world_store.list_domain_events())
        assert len(events) >= 1
        assert events[0]["event_type"] == "StoryCreated"

    def test_event_envelope_is_complete(self, world_store, sample_story):
        run(world_store.add_story(sample_story))
        event = run(world_store.list_domain_events())[0]
        for field in ("event_id", "event_type", "aggregate_type", "aggregate_id",
                      "sequence_number", "occurred_at", "recorded_at"):
            assert event[field] is not None

    def test_sequence_numbers_increase(self, world_store):
        run(world_store.add_story(Story(canonical_title="A")))
        run(world_store.add_story(Story(canonical_title="B")))
        events = run(world_store.list_domain_events())
        sequences = [e["sequence_number"] for e in events]
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences)

    @pytest.mark.parametrize("statement", [
        "DELETE FROM domain_events",
        "UPDATE domain_events SET event_type = 'x'",
        "UPDATE articles SET title = 'x'",
        "DELETE FROM articles",
        "UPDATE claims SET confidence = 9",
        "DELETE FROM claims",
        "UPDATE corrections SET reason = 'x'",
        "DELETE FROM corrections",
        "UPDATE world_snapshots SET story_count = 99",
        "DELETE FROM world_snapshots",
    ])
    def test_historical_tables_reject_mutation(self, world_store, sample_source,
                                               sample_article, sample_claim,
                                               sample_correction, statement):
        """§3.3: enforced by the database, not by convention.

        Each table is populated first — SQLite skips BEFORE triggers on empty
        tables, so an unpopulated table would falsely appear unguarded.
        """
        run(world_store.add_source(sample_source))
        sample_article.source_id = sample_source.id
        article_id = run(world_store.add_article(sample_article))
        claim_id = run(world_store.add_claim(sample_claim))
        sample_correction.target_id = claim_id
        run(world_store.add_correction(sample_correction))
        evidence = Evidence(
            article_id=article_id, source_id=sample_source.id, excerpt="passage")
        run(world_store.add_evidence(evidence))
        run(world_store.create_snapshot())
        run(world_store.add_story(Story(canonical_title="A story")))

        conn = world_store.db.connect()
        with pytest.raises(Exception):  # noqa: B017
            conn.execute(statement)
            conn.commit()

    def test_evidence_rejects_mutation(self, world_store, sample_source,
                                       sample_article):
        run(world_store.add_source(sample_source))
        sample_article.source_id = sample_source.id
        article_id = run(world_store.add_article(sample_article))
        run(world_store.add_evidence(Evidence(
            article_id=article_id, source_id=sample_source.id, excerpt="p")))
        conn = world_store.db.connect()
        with pytest.raises(Exception):  # noqa: B017
            conn.execute("UPDATE evidence SET excerpt = 'x'")
            conn.commit()

    def test_operational_tables_stay_mutable(self, world_store, sample_source):
        """Append-only applies to historical records, not operational state."""
        source_id = run(world_store.add_source(sample_source))
        run(world_store.record_source_attempt(source_id, success=False))
        run(world_store.enqueue_outbox("compile", {"article_id": "a1"}))
        assert run(world_store.get_source(source_id)).failure_count == 1

    def test_filter_events_by_aggregate(self, world_store, sample_story):
        run(world_store.add_story(sample_story))
        events = run(world_store.list_domain_events(
            aggregate_type="story", aggregate_id=sample_story.id))
        assert len(events) == 1


# ── Idempotency ──────────────────────────────────────────────────────


class TestIdempotency:
    def test_key_roundtrip(self, world_store):
        assert world_store.check_idempotency("k1") is None
        world_store.record_idempotency("k1", "claim-extraction", "result-1")
        assert world_store.check_idempotency("k1") == "result-1"

    def test_duplicate_key_is_ignored(self, world_store):
        world_store.record_idempotency("k1", "op", "r1")
        world_store.record_idempotency("k1", "op", "r2")
        assert world_store.check_idempotency("k1") == "r1"


# ── Outbox ───────────────────────────────────────────────────────────


class TestOutbox:
    def test_enqueue_and_drain(self, world_store):
        run(world_store.enqueue_outbox("compile-article", {"article_id": "a1"}))
        pending = run(world_store.pending_outbox())
        assert len(pending) == 1
        assert pending[0]["message_type"] == "compile-article"

    def test_mark_published(self, world_store):
        run(world_store.enqueue_outbox("compile-article", {"article_id": "a1"}))
        pending = run(world_store.pending_outbox())
        run(world_store.mark_outbox_published(pending[0]["id"]))
        assert run(world_store.pending_outbox()) == []


# ── Helpers ──────────────────────────────────────────────────────────


class TestHelpers:
    def test_content_hash_is_stable(self):
        assert content_hash("abc") == content_hash("abc")
        assert content_hash("abc") != content_hash("abd")

    def test_content_hash_length(self):
        assert len(content_hash("x")) == 64
