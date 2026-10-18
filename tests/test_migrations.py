"""Migration and lifecycle tests.

Gate 1: persistence and migrations are reliable. Spec §5.1, §6.1, §6.7, §24.5.
"""

import asyncio
import os
import sqlite3

import pytest

from core.lifecycle import Lifecycle, RecoveryError, RecoveryIssue
from core.migrations import (
    Migration,
    MigrationError,
    MigrationRunner,
    default_migrations,
)
from core.models import Source, Story
from core.persistence import Database, WorldStore


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Migration runner ─────────────────────────────────────────────────


class TestMigrationRunner:
    def test_fresh_database_starts_at_zero(self, db_path):
        db = Database(db_path)
        try:
            runner = MigrationRunner(db, default_migrations())
            assert runner.current_version() == 0
        finally:
            db.close()

    def test_migrate_applies_pending(self, db_path):
        db = Database(db_path)
        try:
            runner = MigrationRunner(db, default_migrations())
            applied = runner.migrate()
            assert applied == [1]
            assert runner.current_version() == 1
        finally:
            db.close()

    def test_migrate_is_idempotent(self, db_path):
        db = Database(db_path)
        try:
            runner = MigrationRunner(db, default_migrations())
            runner.migrate()
            assert runner.migrate() == [], "re-migrating must apply nothing"
        finally:
            db.close()

    def test_migrations_survive_reopen(self, db_path):
        db = Database(db_path)
        MigrationRunner(db, default_migrations()).migrate()
        db.close()

        reopened = Database(db_path)
        try:
            runner = MigrationRunner(reopened, default_migrations())
            assert runner.current_version() == 1
            assert runner.pending() == []
        finally:
            reopened.close()

    def test_ledger_does_not_accumulate_on_reopen(self, db_path):
        for _ in range(3):
            db = Database(db_path)
            MigrationRunner(db, default_migrations()).migrate()
            db.close()
        db = Database(db_path)
        try:
            rows = db.connect().execute(
                "SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
            assert rows == 1
        finally:
            db.close()

    def test_migrate_to_target_stops_early(self, db_path):
        def up_a(conn):
            conn.execute("CREATE TABLE t_a (x INTEGER)")

        def up_b(conn):
            conn.execute("CREATE TABLE t_b (x INTEGER)")

        migrations = [
            Migration(version=1, name="a", up=up_a),
            Migration(version=2, name="b", up=up_b),
        ]
        db = Database(db_path)
        try:
            runner = MigrationRunner(db, migrations)
            applied = runner.migrate(target=1)
            assert applied == [1]
            assert runner.current_version() == 1
        finally:
            db.close()

    def test_rollback_restores_prior_state(self, db_path):
        def up(conn):
            conn.execute("CREATE TABLE t_a (x INTEGER)")

        def down(conn):
            conn.execute("DROP TABLE IF EXISTS t_a")

        migrations = [Migration(version=1, name="a", up=up, down=down)]
        db = Database(db_path)
        try:
            runner = MigrationRunner(db, migrations)
            runner.migrate()
            assert runner.rollback(0) == [1]
            assert runner.current_version() == 0
            tables = {
                r[0] for r in db.connect().execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert "t_a" not in tables
        finally:
            db.close()

    def test_rollback_without_down_raises(self, db_path):
        migrations = [
            Migration(version=1, name="no_down",
                      up=lambda conn: conn.execute("CREATE TABLE t (x INTEGER)"))
        ]
        db = Database(db_path)
        try:
            runner = MigrationRunner(db, migrations)
            runner.migrate()
            with pytest.raises(MigrationError):
                runner.rollback(0)
        finally:
            db.close()

    def test_failed_migration_leaves_ledger_clean(self, db_path):
        def bad_up(conn):
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.execute("INSERT INTO nonexistent VALUES (1)")

        migrations = [Migration(version=1, name="bad", up=bad_up)]
        db = Database(db_path)
        try:
            runner = MigrationRunner(db, migrations)
            with pytest.raises(MigrationError):
                runner.migrate()
            assert runner.current_version() == 0, \
                "a failed migration must not be recorded as applied"
            tables = {
                r[0] for r in db.connect().execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert "t" not in tables, "the failed migration rolled back"
        finally:
            db.close()

    def test_migrations_apply_in_version_order(self, db_path):
        order = []

        def make(name):
            def up(conn):
                order.append(name)
            return up

        migrations = [
            Migration(version=3, name="c", up=make("c")),
            Migration(version=1, name="a", up=make("a")),
            Migration(version=2, name="b", up=make("b")),
        ]
        db = Database(db_path)
        try:
            MigrationRunner(db, migrations).migrate()
            assert order == ["a", "b", "c"]
        finally:
            db.close()

    def test_status_reports_applied_state(self, db_path):
        db = Database(db_path)
        try:
            runner = MigrationRunner(db, default_migrations())
            runner.migrate()
            status = runner.status()
            assert status[0]["version"] == 1
            assert status[0]["applied"] is True
        finally:
            db.close()

    def test_default_migration_creates_the_full_schema(self, db_path):
        db = Database(db_path)
        try:
            MigrationRunner(db, default_migrations()).migrate()
            tables = {
                r[0] for r in db.connect().execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
            expected = {
                "sources", "ownership_groups", "articles", "entities",
                "events", "stories", "claims", "evidence", "claim_evidence",
                "corrections", "world_snapshots", "domain_events",
                "outbox_messages", "idempotency_keys", "schema_migrations",
            }
            assert expected <= tables
        finally:
            db.close()


# ── Lifecycle and recovery ───────────────────────────────────────────


class TestLifecycleRecovery:
    def test_start_runs_all_ten_steps(self, db_path):
        with Lifecycle(db_path) as lifecycle:
            report = lifecycle.last_recovery
            assert report is not None
            assert len(report.steps_completed) == 10

    def test_start_enters_idle_mode(self, db_path):
        with Lifecycle(db_path) as lifecycle:
            assert lifecycle.mode.value == "idle"

    def test_clean_start_reports_no_issues(self, db_path):
        with Lifecycle(db_path) as lifecycle:
            assert lifecycle.last_recovery.issues == []
            assert lifecycle.last_recovery.ok

    def test_stop_returns_to_stopped(self, db_path):
        lifecycle = Lifecycle(db_path)
        lifecycle.start()
        lifecycle.stop()
        assert lifecycle.mode.value == "stopped"

    def test_stop_is_idempotent(self, db_path):
        lifecycle = Lifecycle(db_path)
        lifecycle.start()
        lifecycle.stop()
        lifecycle.stop()
        assert lifecycle.mode.value == "stopped"

    def test_recovery_report_is_persisted(self, db_path):
        with Lifecycle(db_path) as lifecycle:
            events = run(lifecycle.store.list_domain_events(
                event_type="RecoveryCompleted"))
            assert len(events) == 1

    def test_restart_applies_no_new_migrations(self, db_path):
        with Lifecycle(db_path) as first:
            assert first.last_recovery.migrations_applied == [1]
        with Lifecycle(db_path) as second:
            assert second.last_recovery.migrations_applied == []

    def test_store_is_usable_after_start(self, db_path):
        with Lifecycle(db_path) as lifecycle:
            source = Source(name="Reuters", endpoint="https://reuters.com/feed")
            source_id = run(lifecycle.store.add_source(source))
            assert run(lifecycle.store.get_source(source_id)) is not None

    def test_orphaned_foreign_key_is_an_integrity_violation(self, db_path):
        """Recovery must refuse, not silently repair."""
        with Lifecycle(db_path) as lifecycle:
            pass
        # Inject an orphan outside the application.
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            """INSERT INTO articles (id, source_id, canonical_url, content_hash,
               first_seen_at, fetched_at)
               VALUES ('orphan', 'ghost-source', 'u', '""" + "z" * 64 + """',
               '2026-01-01', '2026-01-01')""")
        conn.commit()
        conn.close()

        lifecycle = Lifecycle(db_path)
        with pytest.raises(RecoveryError):
            lifecycle.start()
        assert lifecycle.mode.value == "degraded"
        lifecycle.stop()

    def test_recovery_detects_missing_snapshot_artifact(self, db_path, tmp_path):
        artifact_dir = str(tmp_path / "snapshots")
        with Lifecycle(db_path) as lifecycle:
            run(lifecycle.store.add_story(Story(canonical_title="S")))
            snapshot = run(lifecycle.store.create_snapshot(
                artifact_dir=artifact_dir))
            reference = snapshot.state_reference
        # Remove the artifact the record points at.
        os.remove(reference)

        lifecycle = Lifecycle(db_path)
        report = lifecycle.start()
        kinds = {i["kind"] for i in report.issues}
        assert RecoveryIssue.MISSING_ARTIFACT.value in kinds
        lifecycle.stop()

    def test_pending_outbox_is_reported_not_lost(self, db_path):
        with Lifecycle(db_path) as lifecycle:
            run(lifecycle.store.enqueue_outbox("compile", {"a": 1}))
        with Lifecycle(db_path) as lifecycle:
            assert lifecycle.last_recovery.pending_outbox == 1


# ── Legacy import ────────────────────────────────────────────────────


LEGACY_DDL = """
CREATE TABLE stories (story_id TEXT PRIMARY KEY, title TEXT, summary TEXT,
    status TEXT, first_seen TIMESTAMP, last_updated TIMESTAMP, entity_ids TEXT,
    claim_ids TEXT, confidence REAL, created_at TIMESTAMP, corrected_by TEXT);
CREATE TABLE events (event_id TEXT PRIMARY KEY, story_id TEXT, source_url TEXT,
    fetched_at TIMESTAMP, title TEXT, content TEXT, published_at TIMESTAMP,
    extractor TEXT, entity_ids TEXT, claim_ids TEXT, epistemic_category TEXT,
    confidence REAL, created_at TIMESTAMP, corrected_by TEXT);
CREATE TABLE claims (claim_id TEXT PRIMARY KEY, claim_text TEXT, status TEXT,
    confidence REAL, evidence_ids TEXT, contradiction_ids TEXT, source_url TEXT,
    extractor TEXT, created_at TIMESTAMP, corrected_by TEXT);
CREATE TABLE evidence (evidence_id TEXT PRIMARY KEY, claim_id TEXT, source_url TEXT,
    content TEXT, extraction_method TEXT, extractor TEXT, reliability REAL,
    epistemic_category TEXT, created_at TIMESTAMP, correction_of TEXT);
CREATE TABLE entities (entity_id TEXT PRIMARY KEY, name TEXT, entity_type TEXT,
    aliases TEXT, first_seen TIMESTAMP, last_seen TIMESTAMP,
    mention_count INTEGER, created_at TIMESTAMP);
CREATE TABLE world_snapshots (snapshot_id TEXT PRIMARY KEY, timestamp TIMESTAMP,
    story_count INT, claim_count INT, evidence_count INT, entity_count INT,
    story_summaries TEXT, claim_summaries TEXT, diff_from_previous TEXT,
    created_at TIMESTAMP);
CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP);
"""


@pytest.fixture
def legacy_db(tmp_path):
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_DDL)
    conn.execute(
        "INSERT INTO stories VALUES ('s1','Climate Summit','Leaders met',"
        "'active','2026-01-01T10:00:00','2026-01-01T10:00:00','[]','[]',"
        "0.7,'2026-01-01T10:00:00',NULL)")
    conn.execute(
        "INSERT INTO events VALUES ('e1','s1','https://reuters.com/a',"
        "'2026-01-01T09:00:00','Summit Begins','World leaders gathered.',"
        "'2026-01-01T08:00:00','rss','[]','[]','claim',0.8,"
        "'2026-01-01T09:00:00',NULL)")
    conn.execute(
        "INSERT INTO events VALUES ('e2','s1','https://ap.org/b',"
        "'2026-01-02T09:00:00','Summit Day 2','Talks continued.',"
        "'2026-01-02T08:00:00','rss','[]','[]','claim',0.8,"
        "'2026-01-02T09:00:00',NULL)")
    conn.execute(
        "INSERT INTO claims VALUES ('c1','Temps rose 1.5C','verified',0.6,"
        "'[]','[]','https://reuters.com/a','rss','2026-01-01T09:00:00',NULL)")
    conn.execute(
        "INSERT INTO evidence VALUES ('ev1','c1','https://reuters.com/a',"
        "'NOAA shows 1.5C.','rss','rss',0.85,'evidence',"
        "'2026-01-01T09:00:00',NULL)")
    conn.execute(
        "INSERT INTO evidence VALUES ('ev2','c1','',"
        "'Publisher restated to 1.4C.','correction','system',0.75,'analysis',"
        "'2026-01-03T09:00:00','c1')")
    conn.execute(
        "INSERT INTO entities VALUES ('en1','COP30','concept',"
        "'[\"Climate Conf\"]','2026-01-01T08:00:00','2026-01-01T10:00:00',5,"
        "'2026-01-01T08:00:00')")
    conn.commit()
    yield conn
    conn.close()


class TestLegacyImport:
    def test_detects_legacy_schema(self, legacy_db):
        from core.legacy_import import is_legacy_database
        assert is_legacy_database(legacy_db) is True

    def test_phase1_database_is_not_legacy(self, world_store):
        from core.legacy_import import is_legacy_database
        assert is_legacy_database(world_store.db.connect()) is False

    def test_import_preserves_all_records(self, world_store, legacy_db):
        from core.legacy_import import import_legacy_schema
        counts = import_legacy_schema(world_store, legacy_db)
        assert counts["sources"] == 2
        assert counts["articles"] == 2
        assert counts["events"] == 1
        assert counts["stories"] == 1
        assert counts["claims"] == 1
        assert counts["evidence"] == 1
        assert counts["corrections"] == 1
        assert counts["entities"] == 1

    def test_legacy_events_become_articles(self, world_store, legacy_db):
        from core.legacy_import import import_legacy_schema
        import_legacy_schema(world_store, legacy_db)
        assert run(world_store.count_articles()) == 2

    def test_legacy_corrections_are_not_evidence(self, world_store, legacy_db):
        """The legacy shortcut must not survive the import."""
        from core.legacy_import import import_legacy_schema
        import_legacy_schema(world_store, legacy_db)
        assert run(world_store.count_evidence()) == 1
        assert run(world_store.count_corrections()) == 1

    def test_imported_timestamps_stay_distinct(self, world_store, legacy_db):
        from core.legacy_import import import_legacy_schema
        import_legacy_schema(world_store, legacy_db)
        articles = run(world_store.list_articles())
        article = articles[0]
        assert article.published_at is not None
        assert article.first_seen_at != article.published_at

    def test_imported_claim_status_maps_to_spec(self, world_store, legacy_db):
        from core.legacy_import import import_legacy_schema
        from core.models import ClaimStatus
        import_legacy_schema(world_store, legacy_db)
        claims = run(world_store.list_claims())
        assert claims[0].epistemic_status is ClaimStatus.CORROBORATED

    def test_sources_are_synthesized_from_domains(self, world_store, legacy_db):
        from core.legacy_import import import_legacy_schema
        import_legacy_schema(world_store, legacy_db)
        sources = run(world_store.list_sources())
        domains = {s.canonical_domain for s in sources}
        assert domains == {"reuters.com", "ap.org"}

    def test_articles_reference_a_real_source(self, world_store, legacy_db):
        from core.legacy_import import import_legacy_schema
        import_legacy_schema(world_store, legacy_db)
        articles = run(world_store.list_articles())
        for article in articles:
            assert run(world_store.get_source(article.source_id)) is not None

    def test_entity_aliases_survive_import(self, world_store, legacy_db):
        from core.legacy_import import import_legacy_schema
        import_legacy_schema(world_store, legacy_db)
        entities = run(world_store.list_entities())
        assert entities[0].aliases == ["Climate Conf"]

    def test_import_passes_integrity_checks(self, db_path, legacy_db):
        """An imported database must pass recovery with no violations."""
        from core.legacy_import import import_legacy_schema
        store = WorldStore(db_path)
        import_legacy_schema(store, legacy_db)
        store.close()

        lifecycle = Lifecycle(db_path)
        try:
            report = lifecycle.start()
            assert report.ok, f"import produced violations: {report.issues}"
        finally:
            lifecycle.stop()
