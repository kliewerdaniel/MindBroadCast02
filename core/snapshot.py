"""World snapshot service.

A thin service over WorldStore.create_snapshot(), plus the snapshot-comparison
conveniences the operator interface will need (spec §5.9, §18.5).
"""

from typing import Optional, List

from .models import WorldSnapshot, SnapshotDiff
from .persistence import WorldStore


class SnapshotService:
    """Creates, compares, and retrieves world snapshots."""

    def __init__(self, world_store: WorldStore, artifact_dir: Optional[str] = None):
        self.world_store = world_store
        self.artifact_dir = artifact_dir

    async def create_snapshot(self) -> WorldSnapshot:
        """Capture current world state as an immutable snapshot."""
        return await self.world_store.create_snapshot(
            artifact_dir=self.artifact_dir
        )

    async def compare_snapshots(self, a_id: str, b_id: str) -> SnapshotDiff:
        """Compare two snapshots and return the diff."""
        return await self.world_store.compare_snapshots(a_id, b_id)

    async def get_latest_snapshot(self) -> Optional[WorldSnapshot]:
        """Get the most recent snapshot, or None if none exist."""
        snapshots = await self.world_store.list_snapshots()
        return snapshots[0] if snapshots else None

    async def get_snapshot_diff_from_previous(
        self, snapshot_id: str
    ) -> Optional[SnapshotDiff]:
        """Diff between a snapshot and its recorded predecessor."""
        target = await self.world_store.get_snapshot(snapshot_id)
        if target is None:
            return None
        if target.parent_snapshot_id is None:
            return None
        return await self.world_store.compare_snapshots(
            target.parent_snapshot_id, snapshot_id
        )

    async def list_snapshots(self) -> List[WorldSnapshot]:
        return await self.world_store.list_snapshots()
