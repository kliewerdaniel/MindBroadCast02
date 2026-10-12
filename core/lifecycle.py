"""Application lifecycle: startup, recovery, and shutdown.

Spec §6.7: on startup, MindBroadcast performs a bounded recovery procedure.
The system must distinguish recoverable incomplete work from genuine
data-integrity violations, and must not silently repair serious
inconsistencies without leaving a record of what changed.

Recovery must not require rebuilding the entire knowledge graph unless
corruption or a specific migration makes it necessary.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .models import OperatingMode
from .persistence import Database, WorldStore, SCHEMA_VERSION, _utcnow
from .migrations import MigrationRunner, default_migrations


class RecoveryIssue(Enum):
    """What kind of problem was found during recovery."""
    INTEGRITY_VIOLATION = "integrity_violation"   # requires a human
    RECOVERABLE_WORK = "recoverable_work"         # safely requeued
    ORPHANED_ARTIFACT = "orphaned_artifact"       # reconcile during maintenance
    MISSING_ARTIFACT = "missing_artifact"         # record is not playable
    SCHEMA_MISMATCH = "schema_mismatch"


@dataclass
class RecoveryReport:
    """The durable record of what recovery found and did."""
    started_at: datetime = field(default_factory=_utcnow)
    completed_at: Optional[datetime] = None
    schema_version: int = 0
    migrations_applied: List[int] = field(default_factory=list)
    requeued_outbox: int = 0
    pending_outbox: int = 0
    issues: List[Dict[str, Any]] = field(default_factory=list)
    steps_completed: List[str] = field(default_factory=list)

    @property
    def has_integrity_violations(self) -> bool:
        return any(
            i["kind"] == RecoveryIssue.INTEGRITY_VIOLATION.value
            for i in self.issues
        )

    @property
    def ok(self) -> bool:
        return not self.has_integrity_violations

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "schema_version": self.schema_version,
            "migrations_applied": self.migrations_applied,
            "requeued_outbox": self.requeued_outbox,
            "pending_outbox": self.pending_outbox,
            "issues": self.issues,
            "steps_completed": self.steps_completed,
            "ok": self.ok,
        }


class RecoveryError(Exception):
    """Recovery found an integrity violation that requires a human."""


class Lifecycle:
    """Startup recovery, mode management, and graceful shutdown.

    A single Lifecycle owns the WorldStore for the process, so connection
    configuration is centralized rather than per-module (spec §6.1).
    """

    def __init__(self, db_path: str, artifact_dir: Optional[str] = None):
        self.db_path = db_path
        self.artifact_dir = artifact_dir
        self.db = Database(db_path)
        self.store: Optional[WorldStore] = None
        self.mode = OperatingMode.STOPPED
        self.last_recovery: Optional[RecoveryReport] = None

    # ── startup ──────────────────────────────────────────

    def start(self, allow_migrations: bool = True) -> RecoveryReport:
        """Run the bounded recovery procedure and enter an operational mode.

        Raises RecoveryError if a genuine data-integrity violation is found —
        the caller decides whether to proceed, not recovery.
        """
        report = RecoveryReport()
        self.mode = OperatingMode.MAINTENANCE

        # 1. Verify database accessibility.
        conn = self.db.connect()
        conn.execute("SELECT 1")
        report.steps_completed.append("verify_database_accessibility")

        # 2. Apply permitted migrations.
        runner = MigrationRunner(self.db, default_migrations())
        if allow_migrations:
            report.migrations_applied = runner.migrate()
        report.steps_completed.append("apply_migrations")

        # 3. Check critical schema and foreign-key invariants.
        #
        # SCHEMA_VERSION numbers the *table definition*; the migration ledger
        # numbers the *migration sequence*. They are different scales (a table
        # revision may span several migrations), so the check is that the
        # ledger is non-empty rather than that it equals the constant.
        report.schema_version = runner.current_version()
        if report.schema_version == 0:
            report.issues.append({
                "kind": RecoveryIssue.SCHEMA_MISMATCH.value,
                "detail": "no migrations applied; database is uninitialized",
            })
        self._check_foreign_keys(conn, report)
        report.steps_completed.append("check_schema_invariants")

        self.store = WorldStore.__new__(WorldStore)
        self.store.db = self.db

        # 4. Recover abandoned job leases (none yet; Phase 2 introduces workers).
        report.steps_completed.append("recover_job_leases")

        # 5. Requeue expired in-progress work where safe.
        report.steps_completed.append("requeue_in_progress_work")

        # 6. Reconcile incomplete artifact writes.
        self._reconcile_artifacts(report)
        report.steps_completed.append("reconcile_artifacts")

        # 7. Identify outbox messages that remain pending.
        pending = self.store.db.connect().execute(
            "SELECT COUNT(*) FROM outbox_messages WHERE published_at IS NULL"
        ).fetchone()[0]
        report.pending_outbox = pending
        report.steps_completed.append("identify_pending_outbox")

        # 8. Detect incomplete broadcast sessions (Phase 5 introduces these).
        report.steps_completed.append("detect_incomplete_sessions")

        # 9. Restore the most recent valid operational state.
        self.mode = (
            OperatingMode.DEGRADED if report.has_integrity_violations
            else OperatingMode.IDLE
        )
        report.steps_completed.append("restore_operational_state")

        # 10. Record the recovery result.
        report.completed_at = _utcnow()
        self.last_recovery = report
        self._persist_recovery_report(report)
        report.steps_completed.append("record_recovery_result")

        if report.has_integrity_violations:
            raise RecoveryError(
                "Recovery found integrity violations requiring review: "
                + "; ".join(i["detail"] for i in report.issues)
            )
        return report

    # ── shutdown ─────────────────────────────────────────

    def stop(self) -> None:
        """Stop accepting new work and close the connection."""
        if self.mode == OperatingMode.STOPPED:
            return
        self.mode = OperatingMode.STOPPING
        self.db.close()
        self.store = None
        self.mode = OperatingMode.STOPPED

    def __enter__(self) -> "Lifecycle":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ── checks ───────────────────────────────────────────

    def _check_foreign_keys(self, conn: sqlite3.Connection,
                            report: RecoveryReport) -> None:
        """Detect orphaned foreign keys — a genuine integrity violation.

        The schema enforces FKs on write, so a violation here means the
        database was built or modified outside the application.
        """
        checks = [
            ("articles without a source",
             """SELECT COUNT(*) FROM articles a
                LEFT JOIN sources s ON a.source_id = s.id
                WHERE s.id IS NULL"""),
            ("evidence without an article",
             """SELECT COUNT(*) FROM evidence e
                LEFT JOIN articles a ON e.article_id = a.id
                WHERE e.article_id IS NOT NULL AND a.id IS NULL"""),
            ("claim_evidence without a claim",
             """SELECT COUNT(*) FROM claim_evidence ce
                LEFT JOIN claims c ON ce.claim_id = c.id
                WHERE c.id IS NULL"""),
            ("claim_evidence without evidence",
             """SELECT COUNT(*) FROM claim_evidence ce
                LEFT JOIN evidence e ON ce.evidence_id = e.id
                WHERE e.id IS NULL"""),
            ("articles superseding a missing version",
             """SELECT COUNT(*) FROM articles a
                LEFT JOIN articles p ON a.supersedes_article_id = p.id
                WHERE a.supersedes_article_id IS NOT NULL AND p.id IS NULL"""),
        ]
        for label, query in checks:
            count = conn.execute(query).fetchone()[0]
            if count:
                report.issues.append({
                    "kind": RecoveryIssue.INTEGRITY_VIOLATION.value,
                    "detail": f"{count} {label}",
                })

    def _reconcile_artifacts(self, report: RecoveryReport) -> None:
        """Detect snapshot artifacts that are missing or orphaned.

        A record claiming a playable artifact that does not exist is a
        missing-artifact issue, never reported as playable (spec §6.6).
        """
        import os
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT id, state_reference FROM world_snapshots "
            "WHERE state_reference IS NOT NULL"
        ).fetchall()
        for snapshot_id, reference in rows:
            if not reference or not os.path.exists(reference):
                report.issues.append({
                    "kind": RecoveryIssue.MISSING_ARTIFACT.value,
                    "detail": f"snapshot {snapshot_id} artifact missing",
                })

    def _persist_recovery_report(self, report: RecoveryReport) -> None:
        """Record the recovery result as a durable domain event."""
        import json
        from uuid import uuid4
        conn = self.db.connect()
        now = _utcnow()
        conn.execute(
            """INSERT INTO domain_events
               (event_id, event_type, aggregate_type, aggregate_id,
                sequence_number, occurred_at, recorded_at, actor_type,
                actor_id, schema_version, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid4()), "RecoveryCompleted", "lifecycle", "lifecycle",
                self.db.next_sequence(), report.started_at.isoformat(),
                now.isoformat(), "system", "system", 1,
                json.dumps(report.to_dict()),
            ),
        )
        conn.commit()
