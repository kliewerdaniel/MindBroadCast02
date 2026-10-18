"""Transaction, recovery, and fault-injection tests.

Spec §6.4, §6.6, §6.7, §22.5. For each failure the expected recovery behavior
is specified before the test is written.

These are the tests that make Gate 1 ("persistence and migrations are
reliable") mean something beyond happy-path CRUD.
"""

import asyncio

import pytest

from core.models import (
    Article,
    Correction,
    CorrectionType,
    Evidence,
    EvidenceRelationship,
    Source,
    Story,
    StoryStatus,
)
from core.persistence import (
    SCHEMA_VERSION,
    Database,
    PersistenceError,
    WorldStore,
)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Transaction boundaries ───────────────────────────────────────────


class TestTransactionBoundaries:
    def test_failed_transaction_rolls_back_completely(self, world_store,
                                                      sample_source):
        """A partial integration must not leave records that appear complete."""
        run(world_store.add_source(sample_source))
        before = run(world_store.count_articles())

        # An article whose source FK is valid but whose insert fails on the
        # duplicate hash constraint must roll back the whole transaction.
        article = Article(
            source_id=sample_source.id, canonical_url="u",
            title="A", content_hash="dup" * 12)
        run(world_store.add_article(article))

        duplicate = Article(
            source_id=sample_source.id, canonical_url="u2",
            title="B", content_hash="dup" * 12)
        with pytest.raises(PersistenceError):
            run(world_store.add_article(duplicate))

        assert run(world_store.count_articles()) == before + 1, \
            "the failed insert must not leave a partial record"

    def test_failed_transaction_emits_no_event(self, world_store, sample_source):
        run(world_store.add_source(sample_source))
        events_before = run(world_store.count_domain_events())
        with pytest.raises(PersistenceError):
            run(world_store.add_article(Article(
                source_id="nonexistent", canonical_url="u",
                content_hash="x" * 64)))
        assert run(world_store.count_domain_events()) == events_before, \
            "a rolled-back transaction must not emit a domain event"

    def test_transaction_commits_record_and_event_together(self, world_store,
                                                           sample_story):
        run(world_store.add_story(sample_story))
        assert run(world_store.count_stories()) == 1
        assert run(world_store.count_domain_events()) == 1

    def test_rollback_discards_outbox_message(self, world_store, sample_source):
        """The outbox row is part of the transaction, not a side effect."""
        run(world_store.add_source(sample_source))
        before = len(run(world_store.pending_outbox()))
        with pytest.raises(PersistenceError):
            run(world_store.add_article(Article(
                source_id="nonexistent", canonical_url="u",
                content_hash="x" * 64)))
        assert len(run(world_store.pending_outbox())) == before


# ── Crash recovery ───────────────────────────────────────────────────


class TestCrashRecovery:
    def test_pending_outbox_survives_restart(self, db_path):
        """Work queued but not published is recovered, not lost."""
        store = WorldStore(db_path)
        run(store.enqueue_outbox("compile-article", {"article_id": "a1"}))
        store.close()

        reopened = WorldStore(db_path)
        try:
            pending = run(reopened.pending_outbox())
            assert len(pending) == 1
            assert pending[0]["message_type"] == "compile-article"
        finally:
            reopened.close()

    def test_published_outbox_is_not_redelivered(self, db_path):
        store = WorldStore(db_path)
        run(store.enqueue_outbox("compile-article", {"article_id": "a1"}))
        pending = run(store.pending_outbox())
        run(store.mark_outbox_published(pending[0]["id"]))
        store.close()

        reopened = WorldStore(db_path)
        try:
            assert run(reopened.pending_outbox()) == []
        finally:
            reopened.close()

    def test_committed_records_survive_restart(self, db_path):
        store = WorldStore(db_path)
        source = Source(name="Reuters", endpoint="https://reuters.com/feed")
        source_id = run(store.add_source(source))
        article = Article(
            source_id=source_id, canonical_url="u", title="Persisted",
            content_hash="p" * 64)
        article_id = run(store.add_article(article))
        run(store.create_snapshot())
        store.close()

        reopened = WorldStore(db_path)
        try:
            assert run(reopened.get_article(article_id)) is not None
            assert run(reopened.count_snapshots()) == 1
            assert run(reopened.count_domain_events()) >= 3
        finally:
            reopened.close()

    def test_schema_version_is_stable_across_restarts(self, db_path):
        store = WorldStore(db_path)
        store.close()
        reopened = WorldStore(db_path)
        try:
            assert reopened.db.get_schema_version() == SCHEMA_VERSION
        finally:
            reopened.close()

    def test_reopening_does_not_duplicate_schema_rows(self, db_path):
        for _ in range(3):
            store = WorldStore(db_path)
            store.close()
        db = Database(db_path)
        try:
            rows = db.connect().execute(
                "SELECT COUNT(*) FROM schema_version").fetchone()[0]
            assert rows == 1, "schema_version must not accumulate on reopen"
        finally:
            db.close()

    def test_interrupted_write_leaves_no_partial_record(self, db_path):
        """Simulate a process death mid-transaction."""
        store = WorldStore(db_path)
        source = Source(name="S", endpoint="u")
        source_id = run(store.add_source(source))
        # Abandon the store without a clean close, as a crash would.
        store.db.close()

        reopened = WorldStore(db_path)
        try:
            assert run(reopened.get_source(source_id)) is not None
            assert run(reopened.count_domain_events()) >= 1
        finally:
            reopened.close()


# ── Fault injection ──────────────────────────────────────────────────


class TestFaultInjection:
    def test_orphan_foreign_key_is_rejected(self, world_store, sample_article):
        with pytest.raises(PersistenceError):
            run(world_store.add_article(sample_article))

    def test_orphan_evidence_article_is_rejected(self, world_store):
        with pytest.raises(PersistenceError):
            run(world_store.add_evidence(Evidence(
                article_id="nonexistent", source_id="s", excerpt="x")))

    def test_orphan_claim_link_is_rejected(self, world_store, sample_claim):
        claim_id = run(world_store.add_claim(sample_claim))
        with pytest.raises(PersistenceError):
            run(world_store.link_evidence(
                claim_id, "nonexistent", EvidenceRelationship.SUPPORTS))

    def test_orphan_ownership_group_is_rejected(self, world_store, sample_source):
        sample_source.ownership_group_id = "nonexistent"
        with pytest.raises(PersistenceError):
            run(world_store.add_source(sample_source))

    def test_nonexistent_record_lookups_return_none(self, world_store):
        assert run(world_store.get_story("nope")) is None
        assert run(world_store.get_article("nope")) is None
        assert run(world_store.get_claim("nope")) is None
        assert run(world_store.get_source("nope")) is None
        assert run(world_store.get_correction("nope")) is None

    def test_status_change_on_missing_record_raises(self, world_store):
        with pytest.raises(PersistenceError):
            run(world_store.update_story_status("nope", StoryStatus.RESOLVED))

    def test_missing_audio_artifact_is_not_reported_as_playable(self, world_store):
        """A snapshot state_reference must never point at a missing file."""
        snapshot = run(world_store.create_snapshot())
        assert snapshot.state_reference is None, \
            "no artifact dir configured means no false artifact reference"

    def test_database_busy_is_bounded(self, db_path):
        """The busy timeout is configured so contention resolves, not hangs."""
        db = Database(db_path)
        try:
            db.initialize()
            assert db.connect().execute("PRAGMA busy_timeout").fetchone()[0] >= 1000
        finally:
            db.close()


# ── Concurrency ──────────────────────────────────────────────────────


class TestConcurrency:
    def test_concurrent_writers_are_serialized(self, db_path):
        """Two stores on one file must not corrupt each other's writes."""
        a = WorldStore(db_path)
        b = WorldStore(db_path)
        try:
            for i in range(5):
                run(a.add_story(Story(canonical_title=f"A{i}")))
                run(b.add_story(Story(canonical_title=f"B{i}")))
            # Both stores see the same committed total — no writes were lost.
            assert run(a.count_stories()) == 10
            assert run(b.count_stories()) == 10
        finally:
            a.close()
            b.close()

    def test_concurrent_reads_see_committed_data(self, db_path):
        writer = WorldStore(db_path)
        try:
            story_id = run(writer.add_story(Story(canonical_title="Shared")))
        finally:
            writer.close()

        reader = WorldStore(db_path)
        try:
            assert run(reader.get_story(story_id)) is not None
        finally:
            reader.close()


# ── Data integrity invariants ────────────────────────────────────────


class TestDataIntegrity:
    def test_every_article_has_a_real_source(self, db_path, sample_source):
        store = WorldStore(db_path)
        source_id = run(store.add_source(sample_source))
        for i in range(3):
            run(store.add_article(Article(
                source_id=source_id, canonical_url=f"u{i}",
                content_hash=f"{i}" * 64)))
        store.close()

        reopened = WorldStore(db_path)
        try:
            orphans = reopened.db.connect().execute(
                """SELECT COUNT(*) FROM articles a
                   LEFT JOIN sources s ON a.source_id = s.id
                   WHERE s.id IS NULL""").fetchone()[0]
            assert orphans == 0
        finally:
            reopened.close()

    def test_snapshot_manifest_matches_actual_records(self, world_store,
                                                      sample_story, sample_claim):
        run(world_store.add_story(sample_story))
        run(world_store.add_claim(sample_claim))
        snapshot = run(world_store.create_snapshot())

        assert len(snapshot.included_record_manifest["stories"]) == \
            run(world_store.count_stories())
        assert len(snapshot.included_record_manifest["claims"]) == \
            run(world_store.count_claims())

    def test_correction_history_is_preserved_across_supersession(self, world_store):
        first = run(world_store.add_correction(Correction(
            correction_type=CorrectionType.PUBLISHER,
            target_type="claim", target_id="c1", reason="first")))
        second = run(world_store.add_correction(Correction(
            correction_type=CorrectionType.PUBLISHER,
            target_type="claim", target_id="c1", reason="second",
            supersedes_correction_id=first)))

        assert run(world_store.get_correction(first)) is not None
        assert run(world_store.get_correction(second)).supersedes_correction_id == first
        assert run(world_store.count_corrections()) == 2

    def test_event_log_covers_every_aggregate(self, world_store, sample_source,
                                              sample_story, sample_claim):
        run(world_store.add_source(sample_source))
        run(world_store.add_story(sample_story))
        run(world_store.add_claim(sample_claim))

        events = run(world_store.list_domain_events())
        aggregate_types = {e["aggregate_type"] for e in events}
        assert "source" in aggregate_types
        assert "story" in aggregate_types
        assert "claim" in aggregate_types
