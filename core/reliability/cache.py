"""Caching with explicit invalidation.

Spec §23.4. "Cached artifacts must have explicit invalidation rules."

Two invalidation rules the spec states directly:

- A cached story summary "should be invalidated when a material claim changes,
  a correction arrives, or its source snapshot becomes obsolete."
- A cached audio artifact "should only be reused when the script version and
  synthesis configuration are compatible."

The audio rule is the stricter one and gets its own class. Reusing audio from
the wrong script version would broadcast stale text — the §15.4 stage 6
temporal-validation failure, reintroduced through a cache. So `AudioCache.get`
returns a miss unless script version, synthesis attempt, and voice config all
match.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class CacheEntry:
    key: str
    value: Any
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    invalidated_at: Optional[datetime] = None

    @property
    def is_valid(self) -> bool:
        return self.invalidated_at is None


class SummaryCache:
    """§23.4: cached story summaries with explicit invalidation.

    Invalidated when a material claim changes, a correction arrives, or the
    source snapshot becomes obsolete.
    """

    def __init__(self):
        self._entries: Dict[str, CacheEntry] = {}
        self._story_to_keys: Dict[str, Set[str]] = {}
        self.invalidation_log: List[Tuple[str, str]] = []

    def put(self, story_id: str, snapshot_id: str, summary: str) -> str:
        key = f"{story_id}:{snapshot_id}"
        self._entries[key] = CacheEntry(key=key, value=summary)
        self._story_to_keys.setdefault(story_id, set()).add(key)
        return key

    def get(self, story_id: str, snapshot_id: str) -> Optional[str]:
        entry = self._entries.get(f"{story_id}:{snapshot_id}")
        if entry is None or not entry.is_valid:
            return None
        return entry.value

    def invalidate_story(self, story_id: str, reason: str) -> int:
        """§23.4: a material claim change or correction invalidates the summary."""
        count = 0
        now = datetime.now(timezone.utc)
        for key in self._story_to_keys.get(story_id, set()):
            entry = self._entries.get(key)
            if entry is not None and entry.is_valid:
                entry.invalidated_at = now
                self.invalidation_log.append((key, reason))
                count += 1
        return count

    def invalidate_snapshot(self, snapshot_id: str, reason: str) -> int:
        """§23.4: an obsolete source snapshot invalidates dependent summaries."""
        count = 0
        now = datetime.now(timezone.utc)
        for key, entry in self._entries.items():
            if snapshot_id in key and entry.is_valid:
                entry.invalidated_at = now
                self.invalidation_log.append((key, reason))
                count += 1
        return count

    def valid_count(self) -> int:
        return sum(1 for e in self._entries.values() if e.is_valid)


@dataclass
class AudioCacheKey:
    """§23.4: the compatibility tuple for audio reuse."""
    script_id: str
    script_version: int
    synthesis_attempt: str
    voice_profile_version: str
    synthesis_config_version: str = ""

    def matches(self, other: "AudioCacheKey") -> bool:
        """Reuse only when every compatibility field matches."""
        return (
            self.script_id == other.script_id
            and self.script_version == other.script_version
            and self.synthesis_attempt == other.synthesis_attempt
            and self.voice_profile_version == other.voice_profile_version
            and self.synthesis_config_version == other.synthesis_config_version
        )


class AudioCache:
    """§23.4: TTS outputs for identical validated text and voice settings.

    The compatibility check is strict. A script-version mismatch is a miss, not
    a hit — reusing audio from an older script version would broadcast stale
    text.
    """

    def __init__(self):
        self._store: Dict[str, Tuple[AudioCacheKey, bytes]] = {}
        self.hits = 0
        self.misses = 0

    def _key(self, k: AudioCacheKey) -> str:
        return (f"{k.script_id}:{k.script_version}:{k.synthesis_attempt}:"
                f"{k.voice_profile_version}:{k.synthesis_config_version}")

    def put(self, key: AudioCacheKey, audio: bytes) -> str:
        self._store[self._key(key)] = (key, audio)
        return self._key(key)

    def get(self, requested: AudioCacheKey) -> Optional[bytes]:
        entry = self._store.get(self._key(requested))
        if entry is None:
            self.misses += 1
            return None
        stored_key, audio = entry
        # Defensive: the dict key already encodes the match, but verify the
        # stored key explicitly so a hash collision cannot return wrong audio.
        if not stored_key.matches(requested):
            self.misses += 1
            return None
        self.hits += 1
        return audio

    def invalidate_script(self, script_id: str) -> int:
        removed = [k for k, (key, _) in self._store.items()
                   if key.script_id == script_id]
        for k in removed:
            del self._store[k]
        return len(removed)


class EntityResolutionCache:
    """§23.4: entity-resolution candidates."""

    def __init__(self, max_size: int = 10000):
        self.max_size = max_size
        self._store: Dict[str, Any] = {}

    def get(self, surface_form: str) -> Optional[Any]:
        return self._store.get(surface_form.lower())

    def put(self, surface_form: str, candidates: Any) -> None:
        if len(self._store) >= self.max_size:
            # Evict oldest inserted (dicts preserve insertion order).
            self._store.pop(next(iter(self._store)))
        self._store[surface_form.lower()] = candidates

    def size(self) -> int:
        return len(self._store)
