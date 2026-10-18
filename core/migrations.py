"""Versioned schema migrations.

Spec §5.1, §6.1, §24.5: all schema changes go through versioned migrations,
applied in order, tracked in a ledger. Model and prompt upgrades are separate
concerns from database schema upgrades — changing an LLM must not silently
regenerate stored claims.

Each migration is an atomic unit: `up()` runs inside a transaction, and either
the whole migration commits or the ledger is untouched.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from .persistence import (
    Database,
    PersistenceError,
    _dt_to_str,
    _utcnow,
)


@dataclass
class Migration:
    version: int
    name: str
    up: Callable[[sqlite3.Connection], None]
    down: Callable[[sqlite3.Connection], None] | None = None
    description: str = ""


class MigrationError(PersistenceError):
    """Migration failure."""


LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    applied_at TIMESTAMP NOT NULL
);
"""


class MigrationRunner:
    """Applies and rolls back versioned migrations against a Database."""

    def __init__(self, db: Database, migrations: list[Migration] | None = None):
        self.db = db
        self.migrations = sorted(
            migrations if migrations is not None else default_migrations(),
            key=lambda m: m.version,
        )
        self._ensure_ledger()

    # ── ledger ───────────────────────────────────────────

    def _ensure_ledger(self) -> None:
        conn = self.db.connect()
        conn.executescript(LEDGER_DDL)
        conn.commit()

    def applied_versions(self) -> list[int]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version ASC"
        ).fetchall()
        return [r[0] for r in rows]

    def current_version(self) -> int:
        applied = self.applied_versions()
        return applied[-1] if applied else 0

    def pending(self) -> list[Migration]:
        applied = set(self.applied_versions())
        return [m for m in self.migrations if m.version not in applied]

    # ── apply / rollback ─────────────────────────────────

    def migrate(self, target: int | None = None) -> list[int]:
        """Apply pending migrations up to `target` (default: all).

        Returns the versions applied, in order. Raises MigrationError on the
        first failure, leaving the ledger consistent with the database.
        """
        pending = self.pending()
        if target is not None:
            pending = [m for m in pending if m.version <= target]

        applied: list[int] = []
        for migration in pending:
            conn = self.db.connect()
            try:
                conn.execute("BEGIN")
                migration.up(conn)
                conn.execute(
                    """INSERT INTO schema_migrations
                       (version, name, description, applied_at)
                       VALUES (?, ?, ?, ?)""",
                    (
                        migration.version, migration.name,
                        migration.description, _dt_to_str(_utcnow()),
                    ),
                )
                conn.commit()
                applied.append(migration.version)
            except Exception as exc:
                conn.rollback()
                raise MigrationError(
                    f"Migration {migration.version} ({migration.name}) failed: {exc}"
                ) from exc
        return applied

    def rollback(self, target: int = 0) -> list[int]:
        """Roll back migrations newer than `target`, newest first."""
        applied = self.applied_versions()
        to_rollback = [v for v in reversed(applied) if v > target]

        by_version = {m.version: m for m in self.migrations}
        rolled: list[int] = []
        for version in to_rollback:
            migration = by_version.get(version)
            if migration is None:
                raise MigrationError(
                    f"Cannot roll back unknown migration version {version}")
            if migration.down is None:
                raise MigrationError(
                    f"Migration {version} ({migration.name}) has no down()")
            conn = self.db.connect()
            try:
                conn.execute("BEGIN")
                migration.down(conn)
                conn.execute(
                    "DELETE FROM schema_migrations WHERE version = ?", (version,))
                conn.commit()
                rolled.append(version)
            except Exception as exc:
                conn.rollback()
                raise MigrationError(
                    f"Rollback of {version} ({migration.name}) failed: {exc}"
                ) from exc
        return rolled

    def status(self) -> list[dict[str, object]]:
        """Human-readable migration status."""
        applied = set(self.applied_versions())
        return [
            {
                "version": m.version,
                "name": m.name,
                "description": m.description,
                "applied": m.version in applied,
            }
            for m in self.migrations
        ]


# ── Default migrations ───────────────────────────────────────────────


def _m001_initial(conn: sqlite3.Connection) -> None:
    """Create the Phase 1 schema.

    The full 15-table schema is created up front rather than in seven
    incremental migrations, because Source must exist before Article, Article
    before Event, and Correction must never be shoehorned into Evidence.
    Building them in dependency order in one atomic migration makes those
    prerequisites structural rather than incidental.
    """
    from .persistence import CREATE_TABLES_SQL
    conn.executescript(CREATE_TABLES_SQL)


def _m001_down(conn: sqlite3.Connection) -> None:
    tables = [
        "world_snapshots", "corrections", "claim_evidence", "evidence",
        "claims", "stories", "events", "entities", "articles", "sources",
        "ownership_groups", "domain_events", "outbox_messages",
        "idempotency_keys", "schema_version",
    ]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")


def default_migrations() -> list[Migration]:
    """The migrations that ship with the schema.

    The legacy import (spec C2) is registered separately via
    legacy_import_migration() because it needs the source database path.
    """
    return [
        Migration(
            version=1,
            name="initial_schema",
            description="Phase 1 canonical domain model (spec §5)",
            up=_m001_initial,
            down=_m001_down,
        ),
    ]
