"""Snapshot service tests.

Gate 4 requirement: world snapshots preserve consistent historical state.
"""

import asyncio
import os

import pytest

from core.models import (
    Claim,
    ClaimType,
    Correction,
    CorrectionType,
    Entity,
    EntityType,
    Evidence,
    Story,
)
from core.persistence import PersistenceError, WorldStore
from core.snapshot import SnapshotService


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def service(world_store):
    return SnapshotService(world_store)


class TestSnapshotCreation:
    def test_empty_world_snapshot(self, service):
        snapshot = run(service.create_snapshot())
        assert snapshot.story_count == 0
        assert snapshot.claim_count == 0
        assert snapshot.evidence_count == 0
        assert snapshot.entity_count == 0
        assert snapshot.article_count == 0
        assert snapshot.included_record_manifest == {
            "articles": [], "stories": [], "claims": [],
            "evidence": [], "entities": [], "corrections": [],
        }

    def test_snapshot_with_data(self, service, world_store):
        run(world_store.add_story(Story(canonical_title="Test Story")))
        run(world_store.add_claim(Claim(
            canonical_proposition="Test claim", claim_type=ClaimType.OBSERVATIONAL)))
        run(world_store.add_entity(Entity(
            canonical_name="Test Entity", entity_type=EntityType.CONCEPT)))

        snapshot = run(service.create_snapshot())

        assert snapshot.story_count == 1
        assert snapshot.claim_count == 1
        assert snapshot.entity_count == 1

    def test_snapshot_has_unique_id(self, service):
        s1 = run(service.create_snapshot())
        s2 = run(service.create_snapshot())
        assert s1.id != s2.id

    def test_snapshot_records_every_record_type(self, service, world_store,
                                                sample_source, sample_article):
        run(world_store.add_source(sample_source))
        sample_article.source_id = sample_source.id
        article_id = run(world_store.add_article(sample_article))
        story_id = run(world_store.add_story(Story(canonical_title="S")))
        claim_id = run(world_store.add_claim(Claim(canonical_proposition="C")))
        entity_id = run(world_store.add_entity(Entity(canonical_name="E")))
        evidence_id = run(world_store.add_evidence(Evidence(
            article_id=article_id, source_id=sample_source.id, excerpt="x")))

        snapshot = run(service.create_snapshot())
        manifest = snapshot.included_record_manifest
        assert article_id in manifest["articles"]
        assert story_id in manifest["stories"]
        assert claim_id in manifest["claims"]
        assert entity_id in manifest["entities"]
        assert evidence_id in manifest["evidence"]


class TestSnapshotComparison:
    def test_compare_empty_snapshots(self, service):
        a = run(service.create_snapshot())
        b = run(service.create_snapshot())
        diff = run(service.compare_snapshots(a.id, b.id))
        assert diff.new_stories == []
        assert diff.new_claims == []
        assert diff.new_articles == []

    def test_compare_with_changes(self, service, world_store):
        a = run(service.create_snapshot())
        story = Story(canonical_title="New story")
        story_id = run(world_store.add_story(story))
        b = run(service.create_snapshot())

        diff = run(service.compare_snapshots(a.id, b.id))
        assert story_id in diff.new_stories

    def test_compare_detects_corrections(self, service, world_store):
        a = run(service.create_snapshot())
        correction_id = run(world_store.add_correction(Correction(
            correction_type=CorrectionType.PUBLISHER,
            target_type="claim", target_id="c1")))
        b = run(service.create_snapshot())
        diff = run(service.compare_snapshots(a.id, b.id))
        assert correction_id in diff.new_corrections

    def test_snapshot_diff_human_readable(self, service, world_store):
        a = run(service.create_snapshot())
        run(world_store.add_story(Story(canonical_title="S")))
        b = run(service.create_snapshot())
        diff = run(service.compare_snapshots(a.id, b.id))
        assert "Stories" in diff.human_readable
        assert "Claims" in diff.human_readable

    def test_compare_nonexistent_snapshot(self, service):
        a = run(service.create_snapshot())
        with pytest.raises(PersistenceError):
            run(service.compare_snapshots(a.id, "nonexistent-id"))


class TestSnapshotRetrieval:
    def test_get_snapshot(self, service, world_store):
        snapshot = run(service.create_snapshot())
        retrieved = run(world_store.get_snapshot(snapshot.id))
        assert retrieved is not None
        assert retrieved.id == snapshot.id

    def test_list_snapshots(self, service):
        for _ in range(3):
            run(service.create_snapshot())
        assert len(run(service.list_snapshots())) == 3

    def test_latest_snapshot(self, service):
        run(service.create_snapshot())
        snap2 = run(service.create_snapshot())
        latest = run(service.get_latest_snapshot())
        assert latest is not None
        assert latest.id == snap2.id

    def test_latest_snapshot_when_none(self, service):
        assert run(service.get_latest_snapshot()) is None

    def test_diff_from_previous(self, service, world_store):
        a = run(service.create_snapshot())
        run(world_store.add_story(Story(canonical_title="S")))
        b = run(service.create_snapshot())
        diff = run(service.get_snapshot_diff_from_previous(b.id))
        assert diff is not None
        assert diff.a_id == a.id

    def test_diff_from_previous_on_first_snapshot(self, service):
        a = run(service.create_snapshot())
        assert run(service.get_snapshot_diff_from_previous(a.id)) is None

    def test_diff_from_previous_unknown_snapshot(self, service):
        assert run(service.get_snapshot_diff_from_previous("nope")) is None


class TestSnapshotArtifacts:
    def test_snapshot_writes_artifact_file(self, world_store, tmp_path):
        service = SnapshotService(
            world_store, artifact_dir=str(tmp_path / "snapshots"))
        run(world_store.add_story(Story(canonical_title="S")))
        snapshot = run(service.create_snapshot())
        assert snapshot.state_reference is not None
        assert os.path.exists(snapshot.state_reference)

    def test_artifact_survives_restart(self, world_store, tmp_path):
        """The snapshot's state is durable, not just its metadata row."""
        service = SnapshotService(
            world_store, artifact_dir=str(tmp_path / "snapshots"))
        run(world_store.add_story(Story(canonical_title="Durable")))
        snapshot = run(service.create_snapshot())
        world_store.close()

        reopened = WorldStore(":memory:")
        try:
            # The artifact on disk is independent of the database.
            assert os.path.exists(snapshot.state_reference)
        finally:
            reopened.close()
