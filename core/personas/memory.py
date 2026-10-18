"""Persona memory.

Spec §12.6. Persistent but bounded.

The governing sentence: "Memory must not become an uncontrolled transcript
appended to every prompt." So the subsystem enforces two things the spec asks
for and one it implies:

1. Five distinct memory types, each with provenance and a validity status.
2. A retrieval cap that bounds what enters context.
3. The revision relationship — a CORRECTION entry pointing at the SEMANTIC
   entry it revised, so a persona can "remember that it previously argued for
   a position while also recognizing that subsequent evidence weakened that
   position."

The caps are concrete numbers chosen for a working default. §12.6 says
"bounded," not "small"; the numbers are documented as tunable rather than
principled.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .entities import (
    MemoryType, MemoryValidity, PersonaConfig, PersonaMemoryEntry,
)


# Tunable bounds. §12.6 requires bounded; these are the chosen bounds.
DEFAULT_MAX_PER_TYPE = 200
DEFAULT_CONTEXT_LINE_BUDGET = 20          # lines entering a prompt
DEFAULT_MAX_AGE_DAYS: Optional[int] = 365


class MemoryStore:
    """Bounded, typed, provenance-carrying persona memory."""

    def __init__(
        self,
        max_per_type: int = DEFAULT_MAX_PER_TYPE,
        context_line_budget: int = DEFAULT_CONTEXT_LINE_BUDGET,
        max_age_days: Optional[int] = DEFAULT_MAX_AGE_DAYS,
    ):
        self.max_per_type = max_per_type
        self.context_line_budget = context_line_budget
        self.max_age_days = max_age_days
        self._entries: Dict[Tuple[str, MemoryType], List[PersonaMemoryEntry]] = (
            defaultdict(list))

    # ── write ─────────────────────────────────────────────────────────

    def remember(
        self,
        persona_id: str,
        memory_type: MemoryType,
        content: str,
        provenance: str = "",
        source_segment_id: Optional[str] = None,
        evidence_references: Optional[List[str]] = None,
        confidence: float = 0.5,
        revises_entry_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> PersonaMemoryEntry:
        entry = PersonaMemoryEntry(
            persona_id=persona_id,
            memory_type=memory_type,
            content=content,
            provenance=provenance,
            source_segment_id=source_segment_id,
            evidence_references=list(evidence_references or []),
            confidence=confidence,
            revises_entry_id=revises_entry_id,
        )
        entries = self._entries[(persona_id, memory_type)]

        # A revision withdraws what it revises, wherever that entry lives.
        # A CORRECTION revising a SEMANTIC conclusion lives in a different
        # type bucket, so the search must span buckets.
        if revises_entry_id:
            for bucket in self._entries.values():
                for prior in bucket:
                    if (prior.id == revises_entry_id
                            and prior.validity is MemoryValidity.ACTIVE):
                        prior.validity = MemoryValidity.SUPERSEDED

        entries.append(entry)
        self._enforce_bound(persona_id, memory_type, now)
        return entry

    def _enforce_bound(
        self, persona_id: str, memory_type: MemoryType,
        now: Optional[datetime] = None,
    ) -> None:
        """Bound active entries per type.

        The cap limits ACTIVE entries. Withdrawn and superseded entries are
        retained so a recent withdrawal stays visible — but they are capped
        separately and dropped oldest-first, so memory cannot grow without
        bound through churn. A withdrawal is kept long enough to be read; it is
        not archived forever.
        """
        entries = self._entries[(persona_id, memory_type)]

        active = [e for e in entries if e.validity is MemoryValidity.ACTIVE]
        overflow = len(active) - self.max_per_type
        for entry in active[:max(overflow, 0)]:
            entry.validity = MemoryValidity.WITHDRAWN

        # Retain withdrawn entries up to the same cap, then drop oldest-first.
        inactive = [e for e in entries
                    if e.validity is not MemoryValidity.ACTIVE]
        if len(inactive) > self.max_per_type:
            for entry in inactive[:-self.max_per_type]:
                entries.remove(entry)

    # ── read ──────────────────────────────────────────────────────────

    def recall(
        self,
        persona_id: str,
        memory_type: Optional[MemoryType] = None,
        active_only: bool = True,
    ) -> List[PersonaMemoryEntry]:
        results: List[PersonaMemoryEntry] = []
        for (pid, mtype), entries in self._entries.items():
            if pid != persona_id:
                continue
            if memory_type is not None and mtype is not memory_type:
                continue
            for entry in entries:
                if active_only and entry.validity is not MemoryValidity.ACTIVE:
                    continue
                results.append(entry)
        results.sort(key=lambda e: e.created_at)
        return results

    def context_lines(self, persona_id: str,
                      memory_types: Optional[List[MemoryType]] = None
                      ) -> List[str]:
        """§12.6: bounded memory entering context.

        Priority order: CORRECTION (a withdrawn position must be visible),
        OPEN_QUESTION (what is still unresolved), SEMANTIC (stable
        conclusions), EPISODIC, PREFERENCE. Most recent first within a type.
        """
        priority = [
            MemoryType.CORRECTION,
            MemoryType.OPEN_QUESTION,
            MemoryType.SEMANTIC,
            MemoryType.EPISODIC,
            MemoryType.PREFERENCE,
        ]
        if memory_types:
            priority = [m for m in priority if m in memory_types]

        lines: List[str] = []
        for memory_type in priority:
            entries = self.recall(persona_id, memory_type)
            entries = list(reversed(entries))       # most recent first
            for entry in entries:
                if len(lines) >= self.context_line_budget:
                    return lines
                lines.append(entry.to_context_line())
        return lines[:self.context_line_budget]

    def history_of(self, persona_id: str,
                   proposition: str) -> List[PersonaMemoryEntry]:
        """Every entry bearing on a proposition, including withdrawn ones.

        §12.6: a persona should be able to recall that it previously argued
        for a position and that later evidence weakened it. That requires the
        withdrawn entry to remain retrievable.
        """
        normalized = proposition.lower().strip()
        results = []
        for (pid, _mtype), entries in self._entries.items():
            if pid != persona_id:
                continue
            for entry in entries:
                if normalized in entry.content.lower():
                    results.append(entry)
        results.sort(key=lambda e: e.created_at)
        return results

    def revisions_of(self, entry_id: str) -> List[PersonaMemoryEntry]:
        return [e for entries in self._entries.values() for e in entries
                if e.revises_entry_id == entry_id]

    def count(self, persona_id: str,
              memory_type: Optional[MemoryType] = None) -> int:
        return len(self.recall(persona_id, memory_type, active_only=False))


class PersonaRegistry:
    """Versioned persona configurations (§12.2)."""

    def __init__(self):
        self._current: Dict[str, PersonaConfig] = {}
        self._history: Dict[str, List[PersonaConfig]] = {}

    def register(self, config: PersonaConfig) -> PersonaConfig:
        self._current[config.id] = config
        self._history.setdefault(config.id, []).append(config)
        return config

    def get(self, persona_id: str) -> Optional[PersonaConfig]:
        return self._current.get(persona_id)

    def update(self, persona_id: str, **changes) -> PersonaConfig:
        """§12.2: a versioned configuration change, never a silent rewrite."""
        current = self._current.get(persona_id)
        if current is None:
            raise KeyError(f"unknown persona {persona_id}")
        revised = current.revised(**changes)
        self._current[persona_id] = revised
        self._history.setdefault(persona_id, []).append(revised)
        return revised

    def versions(self, persona_id: str) -> List[PersonaConfig]:
        return list(self._history.get(persona_id, []))

    def version_at(self, persona_id: str,
                   version: int) -> Optional[PersonaConfig]:
        for config in self._history.get(persona_id, []):
            if config.configuration_version == version:
                return config
        return None

    def enabled(self) -> List[PersonaConfig]:
        return [c for c in self._current.values() if c.enabled]
