"""User-editable station settings, persisted beside the world model.

The station needs a handful of knobs the user can change without editing
Python: how often it polls, whether it uses the curated world registry or only
feeds the user added, how many feeds it will keep, and whether it speaks in
the multi-persona dialogue voice. These live in a JSON file in the data dir
(``~/.mindbroadcast/settings.json`` for a packaged app) rather than in the
database, because they are configuration, not world state — resetting the
world model must not silently reset the user's choices, and vice versa.

Every read is fail-closed: a missing, unreadable, or malformed file yields the
defaults rather than raising, so a bad edit can never stop the station from
starting. Unknown keys are preserved on write so a newer version's settings
survive a round-trip through an older one.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _default_data_dir() -> Path:
    """The same location the console and desktop app use.

    Deliberately delegates to the console's resolver rather than re-deriving
    the frozen/source decision here. Three copies of that decision is how the
    window and its server ended up with different stores once already; a
    settings file written to one location and read from another is the same
    failure with a smaller blast radius.
    """
    from .console import _data_dir

    return _data_dir()


#: A conservative floor. Polling faster than this hammers publishers and
#: produces no more news than a slower cadence; the station is not a scraper.
MIN_POLL_SECONDS = 30.0


@dataclass
class StationSettings:
    """Everything the user can tune, with safe defaults."""

    #: Seconds between cycles. 30s is the default AND the floor: the station
    #: rotates across ~125 feeds, so a faster cadence would mostly re-ask
    #: publishers for the same headlines.
    poll_seconds: float = 30.0
    #: Use the curated world registry (region-balanced) in addition to the
    #: user's own feeds.
    use_world_feeds: bool = True
    #: Speak multi-persona dialogue when a story has enough substance.
    multi_persona: bool = True
    #: Per-cycle ceiling on articles compiled (each is an LLM pass).
    max_compile_per_cycle: int = 8
    #: Regions to include from the world registry. Empty means all.
    regions: list[str] = field(default_factory=list)
    #: Preserved unknown keys, so a newer version's settings are not dropped.
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "StationSettings":
        data = data or {}
        known = {"poll_seconds", "use_world_feeds", "multi_persona",
                 "max_compile_per_cycle", "regions"}

        def _float(key: str, default: float) -> float:
            try:
                value = float(data.get(key, default))
            except (TypeError, ValueError):
                return default
            # A zero or negative poll would spin the loop; clamp to the floor.
            return max(MIN_POLL_SECONDS, value)

        def _int(key: str, default: int, low: int, high: int) -> int:
            try:
                value = int(data.get(key, default))
            except (TypeError, ValueError):
                return default
            return max(low, min(high, value))

        regions = data.get("regions")
        if not isinstance(regions, list):
            regions = []

        return cls(
            poll_seconds=_float("poll_seconds", 30.0),
            use_world_feeds=bool(data.get("use_world_feeds", True)),
            multi_persona=bool(data.get("multi_persona", True)),
            max_compile_per_cycle=_int("max_compile_per_cycle", 8, 1, 100),
            regions=[str(r) for r in regions],
            extra={k: v for k, v in data.items() if k not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        base = {k: v for k, v in asdict(self).items() if k != "extra"}
        base.update(self.extra)
        return base


class SettingsStore:
    """Load and save StationSettings, tolerating every kind of bad input."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else _default_data_dir() / "settings.json"

    def load(self) -> StationSettings:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return StationSettings()
        if not isinstance(raw, dict):
            return StationSettings()
        return StationSettings.from_mapping(raw)

    def save(self, settings: StationSettings) -> Path:
        """Write atomically, so a crash cannot leave a truncated file.

        A half-written settings file would fail every later load and silently
        revert the user's choices to defaults.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(settings.to_dict(), indent=2,
                                  sort_keys=True))
        os.replace(tmp, self.path)
        return self.path


#: Process-wide store, so the console and any other caller agree.
settings_store = SettingsStore()
